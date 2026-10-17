/* 房间管理工作台：3D 点云 + 结构/尺寸 + 风险评估 + 改造助手入口 + 方案库 */

import { api } from '../api.js?v=house-flow-1';
import { icon } from '../icons.js';
import {
  esc, toast, fmtMeters, fmtScore, fmtPct, riskLevel, scoreTone, typeName,
  formatMeasurement,
  measuredSizeMap, normalizeInstanceId,
  lightbox, emptyState, confirmDialog,
  SCAN_STAGE_LABEL, SCAN_STAGES, scanStageIndex,
} from '../ui.js';
import { scoreRingHTML, mountRings, barRowHTML, mountBars } from '../charts.js';
import { createViewer } from '../viewer/pointcloud.js';
import { normalizeStructure } from '../viewer/structure.mjs';
import { navigate, setLastScan } from '../app.js';
import { houseView } from './house.js?v=house-flow-1';

const ACTIVE_STATUSES = new Set(['uploading', 'extracting', 'reconstructing', 'cleaning', 'understanding']);
const REFERENCE_LABEL = { door: '门', bed: '床', sofa: '沙发', table: '桌', cabinet: '柜', bookshelf: '书架' };
const DIMENSION_LABEL = { width: '宽', height: '高', length: '长' };
const RISK_TYPE_LABEL = { mobility: '通行能力', layout: '空间布局', usage_safety: '使用安全' };
const SEVERITY_ORDER = { high: 0, medium: 1, low: 2, unknown: 3 };

export async function loadRoomData(scanId) {
  const scan = await api.getScan(scanId);
  const data = {
    scan, project: null, report: null, risk: null, measurements: null,
    layout: null, structure: null,
  };
  const tasks = [api.getProject(scan.project_id).then((project) => { data.project = project; }).catch(() => {})];
  if (scan.status === 'done') {
    tasks.push(
      api.getReport(scanId).then((report) => {
        data.report = report;
        data.risk = report?.measures?.risk_assessment || null;
        data.measurements = report?.measures?.measurements || null;
      }).catch(() => {}),
      api.getLayout(scanId).then((layout) => { data.layout = layout; }).catch(() => {}),
      api.getStructure(scanId).then((structure) => { data.structure = structure; }).catch(() => {}),
    );
  }
  await Promise.all(tasks);
  return data;
}

export async function roomView(ctx, scanId) {
  const selectedScan = await api.getScan(scanId);
  if (['house', 'house_demo'].includes(selectedScan.capture_type)) return houseView(ctx, scanId, selectedScan);
  setLastScan(scanId);
  let data = null;
  let viewer = null;
  let pollTimer = null;

  ctx.title([
    { label: '房间工作台', href: '#/projects' },
    { label: `房间 #${scanId}`, current: true },
  ]);
  ctx.actions.innerHTML = '';
  const refreshBtn = document.createElement('button');
  refreshBtn.className = 'icon-btn';
  refreshBtn.dataset.tip = '刷新数据';
  refreshBtn.setAttribute('aria-label', '刷新');
  refreshBtn.innerHTML = icon('refresh', 17);
  refreshBtn.addEventListener('click', () => reload(true));
  ctx.actions.appendChild(refreshBtn);

  ctx.cleanup(() => {
    clearInterval(pollTimer);
    viewer?.dispose();
    viewer = null;
  });

  ctx.host.innerHTML = `
    <div class="room-layout">
      <div class="room-body view-room">
        <div class="room-col" id="room-left">
          ${[0, 1].map(() => `<div class="glass panel"><div class="skeleton-line shimmer" style="width:46%"></div><div class="skeleton-line shimmer" style="width:82%"></div><div class="skeleton-line shimmer" style="width:64%"></div></div>`).join('')}
        </div>
        <div class="room-stage corner-frame">
          <div class="stage-overlay">
            <div class="stage-loading-card">
              <div class="spin" style="color:var(--accent)">${icon('radar', 34)}</div>
              <div class="load-title">正在准备房间工作台</div>
              <div class="load-sub">加载评估数据与三维场景…</div>
            </div>
          </div>
        </div>
        <div class="room-col" id="room-right">
          ${[0, 1].map(() => `<div class="glass panel"><div class="skeleton-line shimmer" style="width:52%"></div><div class="skeleton-line shimmer" style="width:88%"></div><div class="skeleton-line shimmer" style="width:70%"></div></div>`).join('')}
        </div>
      </div>
    </div>`;

  await reload();
  pollTimer = setInterval(async () => {
    if (!data || !ACTIVE_STATUSES.has(data.scan.status)) return;
    const scan = await api.getScan(scanId).catch(() => null);
    if (!scan) return;
    if (scan.status !== data.scan.status || scan.progress !== data.scan.progress) {
      data.scan = scan;
      if (scan.status === 'done') { await reload(); return; }
      renderProcessing();
    }
  }, 5000);

  async function reload(withToast = false) {
    viewer?.dispose();
    viewer = null;
    data = await loadRoomData(scanId);
    render();
    if (withToast) toast('ok', '已刷新', '房间数据已同步');
  }

  function render() {
    const { scan, project } = data;
    const done = scan.status === 'done';
    const failed = scan.status === 'failed';
    ctx.host.innerHTML = `
      <div class="room-layout">
        <div class="room-topline rise">
          <div>
            <div class="row gap-3">
              <h1>房间 #${scan.id}</h1>
              ${done ? '<span class="badge ok"><span class="dot"></span>已完成</span>'
                : failed ? '<span class="badge bad"><span class="dot"></span>处理失败</span>'
                : `<span class="badge info"><span class="dot"></span>${esc(SCAN_STAGE_LABEL[scan.status] || scan.status)}</span>`}
            </div>
            <div class="muted" style="font-size:12.5px;margin-top:4px">
              ${project ? `${icon('folder', 12)} ${esc(project.name)} · ` : ''}视频扫描 · 扫描编号 #${scan.id}
            </div>
          </div>
          <div class="rt-actions">
            ${done ? `<button class="btn" id="btn-room-assistant">${icon('wand', 15)} AI 改造助手</button>` : ''}
            ${done ? `<button class="btn" id="btn-room-pdf">${icon('download', 15)} 报告 PDF</button>` : ''}
            <button class="btn btn-primary" id="btn-room-screen">${icon('monitor', 15)} 大屏模式</button>
          </div>
        </div>
        <div class="room-body view-room">
          <div class="room-col" id="room-left"></div>
          <div class="room-stage corner-frame" id="room-stage">
            <div id="stage-host"></div>
            ${done ? stageChrome() : ''}
            <div class="stage-overlay" id="stage-overlay">
              ${done ? `
              <div class="stage-loading-card">
                <div class="spin" style="color:var(--accent-2)">${icon('radar', 34)}</div>
                <div class="load-title" id="load-title">初始化 3D 引擎</div>
                <div class="progress" style="width:300px"><div class="p-fill" id="load-fill"></div></div>
                <div class="load-sub" id="load-sub">准备加载稠密点云…</div>
              </div>` : `
              <div class="stage-loading-card">
                <div class="float-slow" style="color:var(--accent-2)">${icon('cpu', 36)}</div>
                <div class="load-title">${failed ? '处理失败' : '正在处理房间数据'}</div>
                <div class="load-sub" id="proc-message">${esc(scan.message || SCAN_STAGE_LABEL[scan.status] || '')}</div>
                ${failed ? `<div class="row gap-2" style="margin-top:6px">
                    <button class="btn btn-sm" id="btn-goto-project">返回项目重试上传</button>
                  </div>` : `
                <div class="progress" style="width:320px"><div class="p-fill" style="width:${Math.max(4, Math.min(100, scan.progress || 0))}%"></div></div>
                <div class="load-sub">完成度 ${scan.progress || 0}% · 页面自动刷新</div>`}
                <div class="row gap-3" style="margin-top:10px" id="proc-steps"></div>
              </div>`}
            </div>
          </div>
          <div class="room-col" id="room-right"></div>
        </div>
      </div>`;

    document.getElementById('btn-room-screen')?.addEventListener('click', () => navigate(`#/screen/${scanId}`));
    document.getElementById('btn-room-pdf')?.addEventListener('click', openPdf);
    document.getElementById('btn-room-assistant')?.addEventListener('click', jumpToAssistant);

    if (done) {
      renderLeft();
      renderRight();
      mountViewer();
      mountRings(ctx.host);
      mountBars(ctx.host);
    } else {
      renderProcessing();
    }
  }

  function jumpToAssistant() {
    navigate(`#/scans/${scanId}/assistant`);
  }

  function stageChrome() {
    const chip = (layer, label, color) =>
      `<button class="chip-toggle on" data-layer="${layer}"><span class="swatch" style="color:${color}"></span>${label}</button>`;
    return `
      <div class="stage-top">
        <div class="mode-switch" id="mode-switch" role="tablist" aria-label="视图模式">
          <div class="ms-thumb"></div>
          <button data-mode="scene" class="active" role="tab">${icon('radar', 15)} 真实场景</button>
          <button data-mode="structure" role="tab">${icon('box', 15)} 空间结构</button>
        </div>
      </div>
      <div class="stage-toolbar">
        <div class="toolbar-group">
          ${chip('pcd', '点云', '#aab4c8')}
          ${chip('walls', '墙', '#4f9cf9')}
          ${chip('doors', '门', '#ffb703')}
          ${chip('windows', '窗', '#5ce98a')}
          ${chip('objects', '家具', '#ff8fa3')}
          ${chip('labels', '标签', '#7dd3fc')}
        </div>
        <div class="toolbar-spacer"></div>
        <div class="toolbar-group">
          <button class="icon-btn" id="btn-point-size" data-tip="点大小">${icon('sliders', 16)}</button>
          <button class="icon-btn is-active" id="btn-autorotate" data-tip="自动旋转">${icon('rotate', 16)}</button>
          <button class="icon-btn" id="btn-reset-view" data-tip="重置视角">${icon('target', 16)}</button>
          <button class="icon-btn" id="btn-shot" data-tip="保存截图">${icon('camera', 16)}</button>
          <button class="icon-btn" id="btn-fullscreen" data-tip="全屏">${icon('maximize', 16)}</button>
        </div>
      </div>
      <div class="stage-popover glass hidden" id="point-size-popover">
        <div class="row" style="justify-content:space-between;font-size:12.5px;color:var(--text-2)">
          <span>点大小</span><span class="num" id="point-size-value">1.0×</span>
        </div>
        <input class="range" id="point-size-range" type="range" min="0.4" max="3" step="0.1" value="1">
      </div>
      <div class="stage-hud glass">
        <div class="hud-row"><span>点数</span><span class="num" id="hud-points">—</span></div>
        <div class="hud-row"><span>房间范围</span><span class="num" id="hud-extent">—</span></div>
      </div>
      <div class="stage-status glass">
        <span class="pulse-dot"></span><span id="hud-scene">场景就绪</span>
      </div>`;
  }

  function renderProcessing() {
    const { scan } = data;
    const failed = scan.status === 'failed';
    const overlay = document.getElementById('stage-overlay');
    if (!overlay) return;
    const idx = scanStageIndex(scan.status);
    const stepNames = ['抽帧', '重建', '后处理', '结构识别', '完成'];
    const stepsHtml = stepNames.map((name, i) => {
      const cls = scan.status === 'done' || i < idx ? 'done' : i === idx && !failed ? 'active' : '';
      return `<div class="pipe-step ${cls}"><div class="pipe-node">${cls === 'done' ? icon('check', 11) : i + 1}</div><span class="pipe-label">${name}</span></div>
        ${i < stepNames.length - 1 ? `<div class="pipe-link ${cls === 'done' ? 'done' : ''}"></div>` : ''}`;
    }).join('');
    const card = overlay.querySelector('.stage-loading-card');
    if (card) {
      const title = card.querySelector('.load-title');
      if (title) title.textContent = failed ? '处理失败' : `正在处理 · ${SCAN_STAGE_LABEL[scan.status] || ''}`;
      const sub = card.querySelector('#proc-message');
      if (sub) sub.textContent = scan.message || SCAN_STAGE_LABEL[scan.status] || '';
      const steps = card.querySelector('#proc-steps');
      if (steps) steps.innerHTML = stepsHtml;
      const fill = card.querySelector('.p-fill');
      if (fill) fill.style.width = `${Math.max(4, Math.min(100, scan.progress || 0))}%`;
    }
    document.getElementById('btn-goto-project')?.addEventListener('click', () => navigate(`#/projects/${data.scan.project_id}`));
    document.getElementById('btn-room-screen')?.addEventListener('click', () => navigate(`#/screen/${scanId}`));
    void SCAN_STAGES;
  }

  function renderLeft() {
    const host = document.getElementById('room-left');
    const { measurements, layout, structure } = data;
    const room = measurements?.room || null;
    const scale = measurements?.scale || null;
    const coverage = measurements?.measurement_coverage || null;
    const references = scale?.references || [];
    const counts = structure?.counts || layout?.counts || {};
    const objects = measurements?.objects || [];
    const scaleLabel = scale?.status === 'metric_references' ? '参考尺寸标定'
      : measurements?.metric_scale_available ? '层高先验标定' : '未标定';

    /* 家具清单：优先审计后的 semantic_instances（与 2D 平面图/测量逐项一致），
       旧 objects 只是候选框，位置/尺寸/旋转必须与显示来源同一条实例记录。 */
    const measured = measuredSizeMap(measurements);
    const structureObjects = normalizeStructure(structure || layout).objects;
    let furnitureRows = [];
    if (structureObjects.length) {
      furnitureRows = structureObjects.map((item) => {
        const match = measured.get(normalizeInstanceId(item.instance_id || item.id || ''));
        const length = match?.length_m ?? (Array.isArray(item.size) ? item.size[0] : null);
        const width = match?.width_m ?? (Array.isArray(item.size) ? item.size[1] : null);
        return {
          name: typeName(item.category || item.instance_id),
          size: length && width ? `${Number(length).toFixed(2)} × ${Number(width).toFixed(2)} m` : '',
        };
      });
    } else {
      const grouped = new Map();
      objects.forEach((item) => {
        const key = item.type || 'object';
        if (!grouped.has(key)) grouped.set(key, []);
        grouped.get(key).push(item);
      });
      furnitureRows = Array.from(grouped.entries()).map(([type, items]) => {
        const first = items[0];
        const size = first?.length_m && first?.width_m
          ? `${Number(first.length_m).toFixed(2)} × ${Number(first.width_m).toFixed(2)} m` : '';
        return { name: `${typeName(type)}${items.length > 1 ? ` ×${items.length}` : ''}`, size };
      });
    }
    const furnitureCount = (structureObjects.length || objects.length) || counts.objects || '—';

    host.innerHTML = `
      <div class="glass panel rise" style="--i:1">
        <div class="panel-head"><div class="panel-title">${icon('ruler', 15)} 房间档案</div></div>
        <div class="info-list">
          <div class="info-row"><span class="i-name">房间尺寸</span><span class="i-value">${room ? `${fmtMeters(room.length_m)} × ${fmtMeters(room.width_m)}` : '—'}</span></div>
          <div class="info-row"><span class="i-name">层高</span><span class="i-value">${room ? fmtMeters(room.height_m) : '—'}</span></div>
          <div class="info-row"><span class="i-name">房间面积</span><span class="i-value">${room ? `${(room.length_m * room.width_m).toFixed(1)} m²` : '—'}</span></div>
          <div class="info-row"><span class="i-name">尺度标定</span><span class="i-value">${scaleLabel}</span></div>
          <div class="info-row"><span class="i-name">测量覆盖</span><span class="i-value">${coverage ? fmtPct(coverage.percent) : '—'}</span></div>
        </div>
        ${references.length ? `
        <div class="divider"></div>
        <div class="eyebrow" style="margin-bottom:8px">参考尺寸</div>
        <div class="row gap-2" style="flex-wrap:wrap">
          ${references.map((ref) => `<span class="tag">${esc(REFERENCE_LABEL[ref.object_type] || ref.object_type)}${esc(DIMENSION_LABEL[ref.dimension] || '')} ${esc(ref.meters)} m</span>`).join('')}
        </div>` : ''}
      </div>
      <div class="glass panel rise" style="--i:2">
        <div class="panel-head">
          <div class="panel-title">${icon('layers', 15)} 结构识别</div>
          <div class="panel-actions"><span class="panel-sub">SpatialLM</span></div>
        </div>
        <div class="struct-grid">
          <div class="struct-tile" style="--st-color:#4f9cf9"><div class="st-icon">${icon('layers', 16)}</div><div class="st-count">${counts.walls ?? '—'}</div><div class="st-name">墙</div></div>
          <div class="struct-tile" style="--st-color:#ffb703"><div class="st-icon">${icon('door', 16)}</div><div class="st-count">${counts.doors ?? '—'}</div><div class="st-name">门</div></div>
          <div class="struct-tile" style="--st-color:#5ce98a"><div class="st-icon">${icon('window', 16)}</div><div class="st-count">${counts.windows ?? '—'}</div><div class="st-name">窗</div></div>
          <div class="struct-tile" style="--st-color:#ff8fa3"><div class="st-icon">${icon('bed', 16)}</div><div class="st-count">${furnitureCount}</div><div class="st-name">家具</div></div>
        </div>
        ${furnitureRows.length ? `
        <div class="divider"></div>
        <div class="info-list">
          ${furnitureRows.slice(0, 7).map((row) => {
            return `<div class="info-row"><span class="i-name">${esc(row.name)}</span><span class="i-value">${esc(row.size)}</span></div>`;
          }).join('')}
        </div>` : ''}
      </div>
      <div class="glass panel rise" style="--i:3">
        <div class="panel-head">
          <div class="panel-title">${icon('image', 15)} 平面图纸</div>
        </div>
        <div class="plan-tabs" role="tablist">
          <button class="plan-tab active" data-plan="structure" role="tab">2D 结构图</button>
          <button class="plan-tab" data-plan="passage" role="tab">通行分析图</button>
        </div>
        <div class="plan-frame" id="plan-frame">
          <div class="plan-loading">正在加载图纸…</div>
        </div>
      </div>`;

    wirePlanTabs();
  }

  function wirePlanTabs() {
    const frame = document.getElementById('plan-frame');
    let current = 'structure';
    let currentUrl = null;

    async function loadPlan(kind) {
      frame.innerHTML = '<div class="plan-loading">正在加载图纸…</div>';
      try {
        const url = await api.planAsset(scanId, kind);
        if (current !== kind) return;
        currentUrl = url;
        frame.innerHTML = `<img src="${url}" alt="${kind === 'passage' ? '通行分析图' : '2D 结构图'}">`;
      } catch {
        frame.innerHTML = `<div class="plan-loading">${icon('image', 22)}<div style="margin-top:8px">${kind === 'passage' ? '通行分析图' : '结构图'}暂不可用</div></div>`;
      }
    }
    frame.addEventListener('click', () => {
      if (currentUrl) lightbox(currentUrl, current === 'passage' ? '通行分析图' : '2D 结构图');
    });
    ctx.host.querySelectorAll('.plan-tab').forEach((tab) => {
      tab.addEventListener('click', () => {
        ctx.host.querySelectorAll('.plan-tab').forEach((node) => node.classList.toggle('active', node === tab));
        current = tab.dataset.plan;
        loadPlan(current);
      });
    });
    loadPlan(current);
  }

  function mountViewer() {
    const stageHost = document.getElementById('stage-host');
    if (!stageHost) return;
    const overlay = document.getElementById('stage-overlay');
    const loadTitle = document.getElementById('load-title');
    const loadSub = document.getElementById('load-sub');
    const loadFill = document.getElementById('load-fill');
    const hudPoints = document.getElementById('hud-points');
    const hudExtent = document.getElementById('hud-extent');
    const hudScene = document.getElementById('hud-scene');

    viewer = createViewer(stageHost, {
      defaultAutoRotate: true,
      onStatus: (status) => {
        if (status.phase === 'error') {
          if (loadTitle) loadTitle.textContent = '3D 场景加载失败';
          if (loadSub) loadSub.textContent = status.message || '';
          if (loadFill) loadFill.style.width = '100%';
          return;
        }
        if (status.phase === 'ready') {
          if (hudScene) hudScene.textContent = '场景就绪';
          overlay?.classList.add('leave');
          return;
        }
        const names = {
          manifest: '获取场景清单', points: '流式加载稠密点云',
          render: '逐块渲染点云',
        };
        if (loadTitle) loadTitle.textContent = names[status.phase] || '加载中';
        if (loadSub && status.message) loadSub.textContent = status.message;
        if (loadFill && typeof status.progress === 'number') {
          loadFill.style.width = `${Math.round(status.progress * 100)}%`;
        }
      },
      onStats: (stats) => {
        if (hudPoints) hudPoints.textContent = formatPoints(stats.points);
        if (hudExtent && stats.extent) {
          const dx = stats.extent.x[1] - stats.extent.x[0];
          const dy = stats.extent.y[1] - stats.extent.y[0];
          const dz = stats.extent.z[1] - stats.extent.z[0];
          hudExtent.textContent = `${dx.toFixed(1)} × ${dy.toFixed(1)} × ${dz.toFixed(1)} m`;
        }
      },
    });
    viewer.load(scanId);

    const modeSwitch = document.getElementById('mode-switch');
    if (modeSwitch) {
      const thumb = modeSwitch.querySelector('.ms-thumb');
      const buttons = Array.from(modeSwitch.querySelectorAll('button'));
      const positionThumb = () => {
        const active = modeSwitch.querySelector('button.active');
        if (!active || !thumb) return;
        thumb.style.left = `${active.offsetLeft}px`;
        thumb.style.width = `${active.offsetWidth}px`;
      };
      requestAnimationFrame(positionThumb);
      const onResize = () => positionThumb();
      window.addEventListener('resize', onResize);
      ctx.cleanup(() => window.removeEventListener('resize', onResize));
      buttons.forEach((btn) => {
        btn.addEventListener('click', () => {
          buttons.forEach((node) => node.classList.toggle('active', node === btn));
          positionThumb();
          viewer.setMode(btn.dataset.mode);
          syncLayerChips(btn.dataset.mode);
          if (btn.dataset.mode === 'structure') {
            document.getElementById('btn-autorotate')?.classList.add('is-active');
            viewer.setAutoRotate(true);
          }
        });
      });
    }

    /* 框架图层（墙/门/窗/家具/标签）只在“空间结构”里渲染；真实场景下 chip 降调并注明。 */
    const frameChips = Array.from(ctx.host.querySelectorAll('.chip-toggle[data-layer]'))
      .filter((chip) => chip.dataset.layer !== 'pcd');
    const syncLayerChips = (mode) => {
      const structure = mode === 'structure';
      frameChips.forEach((chip) => {
        chip.classList.toggle('is-muted', !structure);
        chip.title = structure ? '' : '框架仅在“空间结构”视图渲染';
      });
    };
    syncLayerChips('scene');

    ctx.host.querySelectorAll('.chip-toggle[data-layer]').forEach((chip) => {
      chip.addEventListener('click', () => {
        const on = !chip.classList.contains('on');
        chip.classList.toggle('on', on);
        viewer.setLayer(chip.dataset.layer, on);
      });
    });
    const popover = document.getElementById('point-size-popover');
    const range = document.getElementById('point-size-range');
    const rangeValue = document.getElementById('point-size-value');
    document.getElementById('btn-point-size')?.addEventListener('click', () => {
      popover.classList.toggle('hidden');
    });
    range?.addEventListener('input', () => {
      const value = Number(range.value);
      viewer.setPointScale(value);
      if (rangeValue) rangeValue.textContent = `${value.toFixed(1)}×`;
      range.style.setProperty('--fill', `${((value - 0.4) / 2.6) * 100}%`);
    });
    const rotateBtn = document.getElementById('btn-autorotate');
    rotateBtn?.addEventListener('click', () => {
      const on = !rotateBtn.classList.contains('is-active');
      rotateBtn.classList.toggle('is-active', on);
      viewer.setAutoRotate(on);
    });
    document.getElementById('btn-reset-view')?.addEventListener('click', () => viewer.resetView());
    document.getElementById('btn-shot')?.addEventListener('click', () => {
      const dataUrl = viewer.screenshot();
      if (!dataUrl) return;
      const link = document.createElement('a');
      link.href = dataUrl;
      link.download = `anjing_room_${scanId}.png`;
      link.click();
      toast('ok', '截图已保存', `anjing_room_${scanId}.png`);
    });
    document.getElementById('btn-fullscreen')?.addEventListener('click', () => {
      const stage = document.getElementById('room-stage');
      if (!document.fullscreenElement) stage.requestFullscreen?.();
      else document.exitFullscreen?.();
    });
  }

  function renderRight() {
    const host = document.getElementById('room-right');
    const risk = data.risk;
    if (!risk) {
      host.innerHTML = `<div class="glass panel">${emptyState({ iconName: 'shield', title: '评估数据不可用', desc: '该房间暂未生成正式风险评估数据。' })}</div>`;
      return;
    }
    const overall = risk.overall || {};
    const categories = risk.category_scores || {};
    const risks = [...(risk.risks || [])].sort((a, b) =>
      (SEVERITY_ORDER[a.risk_level] ?? 3) - (SEVERITY_ORDER[b.risk_level] ?? 3));
    const counts = { high: 0, medium: 0, low: 0 };
    risks.forEach((item) => { if (counts[item.risk_level] !== undefined) counts[item.risk_level] += 1; });
    const metrics = risk.key_metrics || [];
    const metricLevels = new Map((risk.risks || []).map((item) => [item.metric_code, item.risk_level]));

    host.innerHTML = `
      <div class="glass panel rise" style="--i:2">
        <div class="panel-head"><div class="panel-title">${icon('gauge', 15)} 综合评分</div>
          <div class="panel-actions"><span class="panel-sub">正式评估链路</span></div>
        </div>
        <div class="score-hero">
          ${scoreRingHTML({ score: overall.score ?? null, size: 142, stroke: 11, label: '综合评分' })}
          <div class="score-side">
            ${barRowHTML({ name: '通行能力', weight: '40%', value: categories.mobility?.score })}
            ${barRowHTML({ name: '空间布局', weight: '30%', value: categories.layout?.score })}
            ${barRowHTML({ name: '使用安全', weight: '30%', value: categories.usage_safety?.score })}
          </div>
        </div>
        <div class="divider"></div>
        <div class="row gap-4" style="font-size:12px;color:var(--text-3)">
          <span>置信度 <span class="num" style="color:var(--text-2)">${overall.confidence === null || overall.confidence === undefined ? '—' : fmtPct(overall.confidence * 100)}</span></span>
          <span>覆盖度 <span class="num" style="color:var(--text-2)">${overall.coverage_percent === undefined ? '—' : fmtPct(overall.coverage_percent)}</span></span>
          <span class="grow"></span>
          <span class="badge ${counts.high ? 'bad' : counts.medium ? 'warn' : 'ok'}">${counts.high ? `${counts.high} 项高风险` : counts.medium ? `${counts.medium} 项中风险` : '未见明显风险'}</span>
        </div>
      </div>
      <div class="glass panel rise" style="--i:3" style="min-height:220px">
        <div class="panel-head">
          <div class="panel-title">${icon('alert', 15)} 风险评估</div>
          <div class="panel-actions" style="font-size:12px;color:var(--text-3)">
            ${counts.high ? `<span class="badge bad">高 ${counts.high}</span>` : ''}
            ${counts.medium ? `<span class="badge warn">中 ${counts.medium}</span>` : ''}
            ${counts.low ? `<span class="badge ok">低 ${counts.low}</span>` : ''}
          </div>
        </div>
        <div class="risk-list" style="max-height:280px">
          ${risks.length ? risks.map((item) => riskItem(item)).join('') : emptyState({ iconName: 'shield', title: '暂无风险项', desc: '当前房间未触发风险规则。' })}
        </div>
      </div>
      <div class="glass panel rise" style="--i:4">
        <div class="panel-head"><div class="panel-title">${icon('activity', 15)} 关键指标</div>
          <div class="panel-actions panel-sub">${metrics.length} 项</div>
        </div>
        <div class="metrics-grid">
          ${metrics.map((metric) => {
            const level = metricLevels.get(metric.metric_code);
            const tone = level === 'high' ? 'bad' : level === 'medium' ? 'warn' : level === 'low' ? 'ok' : 'unknown';
            const measured = formatMeasurement(metric.value, metric.unit);
            return `<div class="metric-tile">
              <div class="m-name">${esc(metric.name)}</div>
              <div class="m-value ${tone}">${measured.text}<span class="unit">${esc(measured.unit)}</span></div>
            </div>`;
          }).join('')}
        </div>
      </div>
      <div class="glass panel rise" style="--i:6" id="scheme-panel">
        <div class="panel-head"><div class="panel-title">${icon('save', 15)} 方案库</div>
          <div class="panel-actions"><button class="btn btn-sm" id="btn-scheme-refresh">${icon('refresh', 13)}</button></div>
        </div>
        <div id="scheme-body"><div class="skeleton-line shimmer" style="width:70%"></div></div>
      </div>`;

    wireRiskItems();
    loadSchemes();
    document.getElementById('btn-scheme-refresh')?.addEventListener('click', () => loadSchemes(true));
  }

  function riskItem(item) {
    const level = riskLevel(item.risk_level);
    const threshold = item.threshold || {};
    const levels = threshold.levels || {};
    const direction = threshold.direction || 'below';
    const guide = levels.medium ?? levels.high ?? null;
    const measured = formatMeasurement(item.measured_value, item.unit);
    const guideValue = guide === null ? null : formatMeasurement(guide, item.unit);
    const guideText = guideValue === null ? '—'
      : direction === 'below' ? `建议 ≥ ${guideValue.text}${guideValue.unit}` : `建议 ≤ ${guideValue.text}${guideValue.unit}`;
    return `
      <div class="risk-item ${level.cls}" data-risk-item>
        <div class="r-main">
          <div class="r-name">${esc(item.risk_name)}
            <span class="badge ${level.tone === 'neutral' ? '' : level.tone}" style="margin-left:6px">${level.label}</span>
          </div>
          <div class="r-meta">
            <span>实测 <span class="num">${measured.text}${measured.unit}</span></span>
            <span>${esc(guideText)}</span>
            <span>${esc(RISK_TYPE_LABEL[item.risk_type] || '')}</span>
          </div>
          ${item.advice ? `<div class="r-advice">${icon('lightbulb', 14)} ${esc(item.advice)}</div>` : ''}
        </div>
        <div class="r-toggle">${icon('chevronRight', 15)}</div>
      </div>`;
  }

  function wireRiskItems() {
    ctx.host.querySelectorAll('[data-risk-item]').forEach((item) => {
      item.addEventListener('click', () => item.classList.toggle('open'));
    });
  }

  async function loadSchemes(refresh = false) {
    const body = document.getElementById('scheme-body');
    if (!body) return;
    if (refresh) body.innerHTML = '<div class="skeleton-line shimmer" style="width:70%"></div>';
    try {
      const dataSchemes = await api.listSchemes(scanId);
      const schemes = dataSchemes.schemes || [];
      if (!schemes.length) {
        body.innerHTML = emptyState({ iconName: 'save', title: '还没有方案', desc: '运行一次模拟，然后保存为方案。' });
        return;
      }
      body.innerHTML = `<div class="scheme-list">
        ${schemes.map((scheme) => `
          <div class="scheme-row" data-scheme="${scheme.id}" role="button" tabindex="0" aria-label="查看方案可视化">
            <div class="sc-main">
              <div class="sc-name">${esc(scheme.name)}</div>
              <div class="sc-meta">
                <span>${esc((scheme.created_at || '').slice(0, 10))}</span>
                <span>${scheme.baseline_score ?? '—'} → ${scheme.score ?? '—'} 分</span>
                ${scheme.stale ? '<span class="badge warn">基于旧数据</span>' : ''}
              </div>
            </div>
            <div class="sc-score">${fmtScore(scheme.score)}</div>
            <div class="sc-actions">
              <button class="icon-btn" data-scheme-action="view" data-id="${scheme.id}" data-tip="方案可视化" aria-label="方案可视化">${icon('image', 15)}</button>
              <button class="icon-btn" data-scheme-action="export" data-id="${scheme.id}" data-tip="导出 PDF" aria-label="导出 PDF">${icon('download', 15)}</button>
              <button class="icon-btn" data-scheme-action="delete" data-id="${scheme.id}" data-tip="删除" aria-label="删除">${icon('trash', 15)}</button>
            </div>
          </div>`).join('')}
      </div>`;
      /* 整行进入方案可视化（干完后的框架图/平面图/通行图），行内按钮各自 stopPropagation */
      body.querySelectorAll('.scheme-row').forEach((row) => {
        const open = () => navigate(`#/scans/${scanId}/schemes/${row.dataset.scheme}`);
        row.addEventListener('click', open);
        row.addEventListener('keydown', (event) => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); }
        });
      });
      body.querySelectorAll('[data-scheme-action]').forEach((btn) => {
        btn.addEventListener('click', async (event) => {
          event.stopPropagation();
          const schemeId = Number(btn.dataset.id);
          if (btn.dataset.schemeAction === 'view') navigate(`#/scans/${scanId}/schemes/${schemeId}`);
          else if (btn.dataset.schemeAction === 'export') await exportScheme(schemeId);
          else {
            const ok = await confirmDialog({ title: '删除方案', message: '删除后不可恢复，确定删除该方案？', confirmText: '删除', danger: true });
            if (!ok) return;
            try {
              await api.deleteScheme(scanId, schemeId);
              toast('ok', '方案已删除');
              loadSchemes(true);
            } catch (error) {
              toast('error', '删除失败', error?.message || '');
            }
          }
        });
      });
    } catch (error) {
      body.innerHTML = `<div class="faint" style="font-size:12.5px">方案库加载失败：${esc(error?.message || '')}</div>`;
    }
  }

  async function exportScheme(schemeId) {
    try {
      toast('info', '正在生成方案图纸…');
      const detail = await api.getScheme(scanId, schemeId);
      const blob = await drawPlanPNG(detail.layout_after, detail.plan?.name || `方案 ${schemeId}`);
      const pdfBlob = await api.exportSchemePdf(scanId, schemeId, blob);
      const url = URL.createObjectURL(pdfBlob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `anjing_scheme_${schemeId}.pdf`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      toast('ok', '方案 PDF 已导出', '包含前后评分与指标变化');
    } catch (error) {
      toast('error', '导出失败', error?.message || '');
    }
  }

  async function openPdf() {
    try {
      const url = await api.reportPdf(scanId);
      window.open(url, '_blank', 'noopener');
    } catch (error) {
      toast('error', '报告 PDF 不可用', error?.message || '');
    }
  }
}

/* —— 方案平面图（Canvas 绘制）：布局数据 → PNG —— */
export function drawPlanPNG(layout, title = '改造后布局') {
  return new Promise((resolve, reject) => {
    try {
      const cssVarRgba = (name, alpha) => {
        const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#ffffff';
        const raw = value.replace('#', '');
        const full = raw.length === 3 ? raw.split('').map((c) => c + c).join('') : raw;
        const n = parseInt(full, 16);
        return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
      };
      const canvas = document.createElement('canvas');
      canvas.width = 1400;
      canvas.height = 1000;
      const g = canvas.getContext('2d');
      g.fillStyle = '#0B1220';
      g.fillRect(0, 0, canvas.width, canvas.height);

      const walls = layout?.walls || layout?.structure?.walls || [];
      const doors = layout?.doors || layout?.structure?.doors || [];
      const windows = layout?.windows || layout?.structure?.windows || [];
      const objects = layout?.objects || layout?.furniture || [];
      const items = [...walls, ...doors, ...windows, ...objects].filter((item) =>
        Array.isArray(item.center) && Array.isArray(item.size));

      let minX = -2, maxX = 2, minY = -2, maxY = 2;
      if (items.length) {
        minX = Math.min(...items.map((i) => i.center[0] - i.size[0] / 2));
        maxX = Math.max(...items.map((i) => i.center[0] + i.size[0] / 2));
        minY = Math.min(...items.map((i) => i.center[1] - i.size[1] / 2));
        maxY = Math.max(...items.map((i) => i.center[1] + i.size[1] / 2));
      }
      const padding = 110;
      const scale = Math.min(
        (canvas.width - padding * 2) / Math.max(maxX - minX, 0.5),
        (canvas.height - padding * 2) / Math.max(maxY - minY, 0.5),
      );
      const ox = padding + ((canvas.width - padding * 2) - (maxX - minX) * scale) / 2;
      const oy = padding + ((canvas.height - padding * 2) - (maxY - minY) * scale) / 2;
      const tx = (x) => ox + (x - minX) * scale;
      const ty = (y) => oy + (maxY - y) * scale;

      // 网格
      g.strokeStyle = 'rgba(148,180,255,0.08)';
      g.lineWidth = 1;
      for (let x = padding; x < canvas.width - padding + 1; x += 40) {
        g.beginPath(); g.moveTo(x, padding); g.lineTo(x, canvas.height - padding); g.stroke();
      }
      for (let y = padding; y < canvas.height - padding + 1; y += 40) {
        g.beginPath(); g.moveTo(padding, y); g.lineTo(canvas.width - padding, y); g.stroke();
      }

      const drawItem = (item, stroke, fill, label) => {
        const [cx, cy] = item.center;
        const [sx, sy] = item.size;
        const angle = ((item.rotation_z_deg || 0) * Math.PI) / 180;
        g.save();
        g.translate(tx(cx), ty(cy));
        g.rotate(-angle);
        const w = sx * scale;
        const h = sy * scale;
        g.fillStyle = fill;
        g.strokeStyle = stroke;
        g.lineWidth = 2;
        g.beginPath();
        g.rect(-w / 2, -h / 2, w, h);
        g.fill();
        g.stroke();
        if (label) {
          g.fillStyle = 'rgba(234,240,251,0.85)';
          g.font = '600 22px "Microsoft YaHei", sans-serif';
          g.textAlign = 'center';
          g.textBaseline = 'middle';
          g.fillText(label, 0, 0);
        }
        g.restore();
      };

      walls.forEach((item) => drawItem(item, cssVarRgba('--wall', 0.9), cssVarRgba('--wall', 0.16), null));
      doors.forEach((item) => drawItem(item, cssVarRgba('--door', 0.95), cssVarRgba('--door', 0.22), null));
      windows.forEach((item) => drawItem(item, cssVarRgba('--window', 0.95), cssVarRgba('--window', 0.22), null));
      objects.forEach((item) => {
        const kind = item.category || String(item.id || '').replace(/[_-]?\d+$/, '').split('_')[0];
        drawItem(item, cssVarRgba('--furniture', 0.9), cssVarRgba('--furniture', 0.1), kind ? typeName(kind) : null);
      });

      // 标题与页脚
      g.textAlign = 'left';
      g.textBaseline = 'alphabetic';
      g.fillStyle = '#EAF0FB';
      g.font = '700 34px "Microsoft YaHei", sans-serif';
      g.fillText(title, padding, 68);
      g.fillStyle = 'rgba(168,180,204,0.9)';
      g.font = '400 20px "Microsoft YaHei", sans-serif';
      g.fillText(`安龄智境 · 沙盒模拟方案平面图 · ${new Date().toLocaleString('zh-CN')}`, padding, canvas.height - 46);
      g.fillStyle = cssVarRgba('--accent', 0.9);
      g.font = '600 18px "Microsoft YaHei", sans-serif';
      g.textAlign = 'right';
      g.fillText('未修改真实房间数据', canvas.width - padding, canvas.height - 46);

      canvas.toBlob((blob) => {
        if (blob) resolve(blob);
        else reject(new Error('平面图生成失败'));
      }, 'image/png');
    } catch (error) {
      reject(error);
    }
  });
}

function formatPoints(count) {
  if (!count) return '—';
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(2)} M`;
  if (count >= 1000) return `${(count / 1000).toFixed(1)} K`;
  return String(count);
}

void fmtPct; void scoreTone;
