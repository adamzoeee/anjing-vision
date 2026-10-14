/* 大屏演示模式：一屏驾驶舱（1920×1080 基准） */

import { api } from '../api.js?v=house-scan-1';
import { icon } from '../icons.js';
import {
  esc, fmtMeters, fmtPct, riskLevel, typeName,
  formatMeasurement,
  SCAN_STAGE_LABEL,
} from '../ui.js';
import { scoreRingHTML, mountRings, barRowHTML, mountBars } from '../charts.js';
import { createViewer } from '../viewer/pointcloud.js';
import { structureFurniture } from '../viewer/structure.mjs';
import { navigate, setLastScan } from '../app.js';
import { loadRoomData } from './room.js';

const RISK_TYPE_LABEL = { mobility: '通行能力', layout: '空间布局', usage_safety: '使用安全' };
const SEVERITY_ORDER = { high: 0, medium: 1, low: 2, unknown: 3 };
const REFERENCE_LABEL = { door: '门', bed: '床', sofa: '沙发', table: '桌', cabinet: '柜', bookshelf: '书架' };
const DIMENSION_LABEL = { width: '宽', height: '高', length: '长' };

export async function screenView(ctx, scanId) {
  const selectedScan = await api.getScan(scanId);
  if (selectedScan.capture_type === 'house_demo') {
    setLastScan(scanId, 'house_demo');
    navigate(`#/house/${scanId}`, { replace: true });
    return;
  }
  setLastScan(scanId);
  let viewer = null;
  let clockTimer = null;
  let pollTimer = null;
  ctx.host.innerHTML = `
    <div class="screen-root">
      <div style="flex:1;display:grid;place-items:center">
        <div class="glass panel" style="width:520px;text-align:center;padding:48px">
          <div class="spin" style="color:var(--accent);margin-bottom:16px">${icon('radar', 40)}</div>
          <h2 style="font-size:19px;font-weight:650">正在进入大屏模式</h2>
          <p class="muted" style="margin-top:10px;font-size:13px">加载评估数据与三维场景…</p>
        </div>
      </div>
    </div>`;
  let data = await loadRoomData(scanId);

  ctx.cleanup(() => {
    viewer?.dispose();
    clearInterval(clockTimer);
    clearInterval(pollTimer);
    document.removeEventListener('keydown', onKey);
  });
  const onKey = (event) => { if (event.key === 'Escape') exit(); };
  document.addEventListener('keydown', onKey);
  const exit = () => navigate(`#/scans/${scanId}`);

  if (data.scan.status !== 'done') {
    renderWaiting();
    return;
  }

  const close = await api.listScans(data.scan.project_id).then((scans) =>
    scans.filter((scan) => scan.status === 'done' && scan.capture_type !== 'house_demo')).catch(() => []);

  render();

  function render() {
    const risk = data.risk || {};
    const overall = risk.overall || {};
    const categories = risk.category_scores || {};
    const risks = [...(risk.risks || [])].sort((a, b) =>
      (SEVERITY_ORDER[a.risk_level] ?? 3) - (SEVERITY_ORDER[b.risk_level] ?? 3));
    const metrics = risk.key_metrics || [];
    const metricLevels = new Map((risk.risks || []).map((item) => [item.metric_code, item.risk_level]));
    const levelCounts = { high: 0, medium: 0, low: 0 };
    risks.forEach((item) => { if (levelCounts[item.risk_level] !== undefined) levelCounts[item.risk_level] += 1; });
    const measurements = data.measurements || {};
    const room = measurements.room || null;
    const scale = measurements.scale || null;
    const references = scale?.references || [];
    const counts = data.structure?.counts || data.layout?.counts || {};
    const structureObjectCount = structureFurniture(data.structure || data.layout).length;
    const projectName = data.project?.name || '未命名项目';

    ctx.host.innerHTML = `
      <div class="aurora aurora-a"></div>
      <div class="aurora aurora-b"></div>
      <div class="screen-root">
        <header class="screen-top glass corner-frame sc-enter-top d1">
          <div class="st-brand">
            <div class="brand-mark">${icon('scanframe', 20)}</div>
            <div>
              <div class="brand-cn glow-text">安龄智境</div>
              <div class="brand-en">ANJING VISION</div>
            </div>
          </div>
          <div class="st-divider"></div>
          <div class="st-room">
            <div class="r-name truncate">${esc(projectName)} · 房间 #${data.scan.id}</div>
            <div class="r-sub">居家适老化安全评估 · 稠密三维重建 + 空间智能分析</div>
          </div>
          <div class="st-spacer"></div>
          ${close.length > 0 ? `
          <select class="select" id="screen-scan-switch" style="width:190px;height:34px" aria-label="切换房间">
            ${close.map((scan) => `<option value="${scan.id}" ${scan.id === scanId ? 'selected' : ''}>房间 #${scan.id}</option>`).join('')}
          </select>` : ''}
          <div class="st-live"><span class="pulse-dot"></span> LIVE · 演示模式</div>
          <div class="st-clock">
            <div class="time" id="screen-clock">--:--:--</div>
            <div class="date" id="screen-date">----</div>
          </div>
          <button class="icon-btn" id="btn-screen-fullscreen" data-tip="全屏" aria-label="全屏">${icon('maximize', 17)}</button>
          <button class="icon-btn" id="btn-screen-exit" data-tip="返回工作台 (Esc)" aria-label="返回工作台">${icon('x', 17)}</button>
        </header>
        <div class="screen-grid">
          <div class="screen-left">
            <section class="glass panel corner-frame sc-enter-left d2">
              <div class="panel-head">
                <div class="panel-title">${icon('layers', 14)} 结构识别</div>
                <div class="panel-actions panel-sub">SpatialLM · 结构化 3D 框</div>
              </div>
              <div class="screen-struct-grid">
                <div class="screen-struct-tile" style="--st-color:#4f9cf9"><div class="st-icon">${icon('layers', 18)}</div><div class="st-count">${counts.walls ?? '—'}</div><div class="st-name">墙</div></div>
                <div class="screen-struct-tile" style="--st-color:#ffb703"><div class="st-icon">${icon('door', 18)}</div><div class="st-count">${counts.doors ?? '—'}</div><div class="st-name">门</div></div>
                <div class="screen-struct-tile" style="--st-color:#5ce98a"><div class="st-icon">${icon('window', 18)}</div><div class="st-count">${counts.windows ?? '—'}</div><div class="st-name">窗</div></div>
                <div class="screen-struct-tile" style="--st-color:#ff8fa3"><div class="st-icon">${icon('bed', 18)}</div><div class="st-count">${structureObjectCount || (measurements.objects || []).length || counts.objects || '—'}</div><div class="st-name">家具</div></div>
              </div>
            </section>
            <section class="glass panel corner-frame sc-enter-left d3">
              <div class="panel-head">
                <div class="panel-title">${icon('ruler', 14)} 房间参考尺寸</div>
              </div>
              <div class="info-list">
                <div class="info-row"><span class="i-name">房间尺寸</span><span class="i-value">${room ? `${fmtMeters(room.length_m)} × ${fmtMeters(room.width_m)}` : '—'}</span></div>
                <div class="info-row"><span class="i-name">层高</span><span class="i-value">${room ? fmtMeters(room.height_m) : '—'}</span></div>
                <div class="info-row"><span class="i-name">房间面积</span><span class="i-value">${room ? `${(room.length_m * room.width_m).toFixed(1)} m²` : '—'}</span></div>
                <div class="info-row"><span class="i-name">测量覆盖</span><span class="i-value">${measurements.measurement_coverage ? fmtPct(measurements.measurement_coverage.percent) : '—'}</span></div>
              </div>
              ${references.length ? `
              <div class="divider"></div>
              <div class="row gap-2" style="flex-wrap:wrap">
                ${references.slice(0, 4).map((ref) => `<span class="tag">${esc(REFERENCE_LABEL[ref.object_type] || ref.object_type)}${esc(DIMENSION_LABEL[ref.dimension] || '')} ${esc(ref.meters)} m</span>`).join('')}
              </div>` : ''}
            </section>
            <section class="glass panel corner-frame sc-enter-left d4" style="flex:1;min-height:0">
              <div class="panel-head">
                <div class="panel-title">${icon('image', 14)} 平面图纸</div>
                <div class="panel-actions">
                  <button class="btn btn-sm" id="screen-plan-structure">结构图</button>
                  <button class="btn btn-sm" id="screen-plan-passage">通行图</button>
                </div>
              </div>
              <div class="screen-plan-frame">
                <div class="plan-loading" id="screen-plan-loading">正在加载图纸…</div>
                <img id="screen-plan-img" alt="平面图纸" style="display:none">
              </div>
            </section>
          </div>
          <section class="screen-center corner-frame sc-enter-scale d3">
            <div id="screen-stage-host"></div>
            <div class="stage-top">
              <div class="mode-switch" id="screen-mode-switch" role="tablist" aria-label="视图模式">
                <div class="ms-thumb"></div>
                <button data-mode="scene" class="active" role="tab">${icon('radar', 15)} 真实场景</button>
                <button data-mode="structure" role="tab">${icon('box', 15)} 空间结构</button>
              </div>
            </div>
            <div class="stage-chip-row">
              <button class="chip-toggle on" data-screen-layer="pcd"><span class="swatch" style="color:#aab4c8"></span>点云</button>
              <button class="chip-toggle on" data-screen-layer="walls"><span class="swatch" style="color:#4f9cf9"></span>墙</button>
              <button class="chip-toggle on" data-screen-layer="doors"><span class="swatch" style="color:#ffb703"></span>门</button>
              <button class="chip-toggle on" data-screen-layer="windows"><span class="swatch" style="color:#5ce98a"></span>窗</button>
              <button class="chip-toggle on" data-screen-layer="objects"><span class="swatch" style="color:#ff8fa3"></span>家具</button>
            </div>
            <div class="screen-watermark">SCAN #${data.scan.id}<br>POINT CLOUD · METRIC</div>
            <div class="screen-hud glass">
              <div class="hud-item"><span>点数</span><span class="num" id="screen-hud-points">加载中…</span></div>
              <div class="hud-item"><span>范围</span><span class="num" id="screen-hud-extent">—</span></div>
            </div>
            <div class="scan-beam"></div>
            <div class="stage-overlay" id="screen-stage-overlay">
              <div class="stage-loading-card">
                <div class="spin" style="color:var(--accent-2)">${icon('radar', 38)}</div>
                <div class="load-title" id="screen-load-title">正在加载三维场景</div>
                <div class="progress" style="width:320px"><div class="p-fill" id="screen-load-fill"></div></div>
                <div class="load-sub" id="screen-load-sub">准备调用点云引擎…</div>
              </div>
            </div>
          </section>
          <div class="screen-right">
            <section class="glass panel corner-frame sc-enter-right d2">
              <div class="panel-head">
                <div class="panel-title">${icon('gauge', 14)} 综合评估</div>
                <div class="panel-actions panel-sub">正式评估链路 · 权重 40/30/30</div>
              </div>
              <div class="screen-score-hero">
                ${scoreRingHTML({ score: overall.score ?? null, size: 158, stroke: 12, label: '综合评分' })}
                <div class="score-meta">
                  ${barRowHTML({ name: '通行能力', weight: '40%', value: categories.mobility?.score })}
                  ${barRowHTML({ name: '空间布局', weight: '30%', value: categories.layout?.score })}
                  ${barRowHTML({ name: '使用安全', weight: '30%', value: categories.usage_safety?.score })}
                </div>
              </div>
              <div class="divider"></div>
              <div class="screen-score-note">
                <span>置信度 <span class="num">${overall.confidence === null || overall.confidence === undefined ? '—' : fmtPct(overall.confidence * 100)}</span></span>
                <span>覆盖度 <span class="num">${overall.coverage_percent === undefined ? '—' : fmtPct(overall.coverage_percent)}</span></span>
                <span>风险项 <span class="num">${risks.length}</span></span>
              </div>
            </section>
            <section class="glass panel corner-frame sc-enter-right d3" style="flex:1;min-height:0">
              <div class="panel-head">
                <div class="panel-title">${icon('alert', 14)} 风险清单</div>
                <div class="panel-actions" style="font-size:12px;gap:6px">
                  ${levelCounts.high ? `<span class="badge bad">高 ${levelCounts.high}</span>` : ''}
                  ${levelCounts.medium ? `<span class="badge warn">中 ${levelCounts.medium}</span>` : ''}
                  ${levelCounts.low ? `<span class="badge ok">低 ${levelCounts.low}</span>` : ''}
                </div>
              </div>
              <div class="screen-risk-list">
                ${risks.length ? risks.slice(0, 6).map((item, index) => {
                  const level = riskLevel(item.risk_level);
                  const measured = formatMeasurement(item.measured_value, item.unit);
                  return `
                  <div class="screen-risk-item ${level.cls}" style="--i:${index}">
                    <div class="sr-rail"></div>
                    <div class="sr-name truncate">${esc(item.risk_name)}</div>
                    <div class="sr-value">${measured.text}${measured.unit}</div>
                    <span class="badge ${level.tone === 'neutral' ? '' : level.tone}">${level.label}</span>
                  </div>`;
                }).join('') : `<div class="faint" style="font-size:13px;padding:12px">当前未触发风险规则</div>`}
              </div>
            </section>
          </div>
          <section class="screen-bottom glass corner-frame sc-enter-up d5">
            <div class="ticker-label">${icon('activity', 15)} 关键指标</div>
            <div class="ticker-track">
              <div class="ticker-items">
                ${[...metrics, ...metrics].map((metric) => {
                  const level = metricLevels.get(metric.metric_code);
                  const tone = level === 'high' ? 'bad' : level === 'medium' ? 'warn' : level === 'low' ? 'ok' : '';
                  const measured = formatMeasurement(metric.value, metric.unit);
                  const value = `${measured.text}${measured.unit}`;
                  return `<span class="ticker-item"><span class="t-name">${esc(metric.name)}</span><span class="t-value ${tone}">${value}</span><span class="ticker-sep">·</span></span>`;
                }).join('')}
              </div>
            </div>
          </section>
        </div>
      </div>`;

    wireScreen();
    mountRings(ctx.host);
    mountBars(ctx.host);
  }

  function wireScreen() {
    document.getElementById('btn-screen-exit')?.addEventListener('click', exit);
    document.getElementById('btn-screen-fullscreen')?.addEventListener('click', () => {
      if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
      else document.exitFullscreen?.();
    });
    document.getElementById('screen-scan-switch')?.addEventListener('change', (event) => {
      navigate(`#/screen/${event.target.value}`);
    });
    clockTimer = setInterval(tickClock, 1000);
    tickClock();

    // 图纸
    const img = document.getElementById('screen-plan-img');
    const loading = document.getElementById('screen-plan-loading');
    const loadPlan = async (kind) => {
      loading.style.display = '';
      img.style.display = 'none';
      loading.textContent = '正在加载图纸…';
      try {
        const url = await api.planAsset(scanId, kind);
        img.src = url;
        img.style.display = 'block';
        loading.style.display = 'none';
      } catch {
        loading.textContent = kind === 'passage' ? '通行分析图暂不可用' : '结构图暂不可用';
      }
    };
    document.getElementById('screen-plan-structure')?.addEventListener('click', () => loadPlan('structure'));
    document.getElementById('screen-plan-passage')?.addEventListener('click', () => loadPlan('passage'));
    loadPlan('structure');

    // 3D
    const stageHost = document.getElementById('screen-stage-host');
    const overlay = document.getElementById('screen-stage-overlay');
    const loadTitle = document.getElementById('screen-load-title');
    const loadSub = document.getElementById('screen-load-sub');
    const loadFill = document.getElementById('screen-load-fill');
    const hudPoints = document.getElementById('screen-hud-points');
    const hudExtent = document.getElementById('screen-hud-extent');
    viewer = createViewer(stageHost, {
      cinematic: true,
      defaultAutoRotate: true,
      onStatus: (status) => {
        if (status.phase === 'error') {
          if (loadTitle) loadTitle.textContent = '3D 场景加载失败';
          if (loadSub) loadSub.textContent = status.message || '';
          return;
        }
        if (status.phase === 'ready') {
          overlay?.classList.add('leave');
          return;
        }
        const names = { manifest: '获取场景清单', points: '流式加载稠密点云', render: '逐块渲染点云' };
        if (loadTitle) loadTitle.textContent = names[status.phase] || '加载中';
        if (loadSub && status.message) loadSub.textContent = status.message;
        if (loadFill && typeof status.progress === 'number') {
          loadFill.style.width = `${Math.round(status.progress * 100)}%`;
        }
      },
      onStats: (stats) => {
        if (hudPoints) {
          hudPoints.textContent = stats.points >= 1_000_000
            ? `${(stats.points / 1_000_000).toFixed(2)} M`
            : stats.points >= 1000 ? `${(stats.points / 1000).toFixed(1)} K` : String(stats.points || '—');
        }
        if (hudExtent && stats.extent) {
          const dx = stats.extent.x[1] - stats.extent.x[0];
          const dy = stats.extent.y[1] - stats.extent.y[0];
          const dz = stats.extent.z[1] - stats.extent.z[0];
          hudExtent.textContent = `${dx.toFixed(1)} × ${dy.toFixed(1)} × ${dz.toFixed(1)} m`;
        }
      },
    });
    viewer.load(scanId);

    const modeSwitch = document.getElementById('screen-mode-switch');
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
          syncScreenChips(btn.dataset.mode);
        });
      });
    }

    /* 框架图层（墙/门/窗/家具）只在“空间结构”里渲染；真实场景下 chip 降调并注明。 */
    const frameChips = Array.from(ctx.host.querySelectorAll('[data-screen-layer]'))
      .filter((chip) => chip.dataset.screenLayer !== 'pcd');
    const syncScreenChips = (mode) => {
      const structure = mode === 'structure';
      frameChips.forEach((chip) => {
        chip.classList.toggle('is-muted', !structure);
        chip.title = structure ? '' : '框架仅在“空间结构”视图渲染';
      });
    };
    syncScreenChips('scene');

    ctx.host.querySelectorAll('[data-screen-layer]').forEach((chip) => {
      chip.addEventListener('click', () => {
        const on = !chip.classList.contains('on');
        chip.classList.toggle('on', on);
        viewer.setLayer(chip.dataset.screenLayer, on);
      });
    });
  }

  function tickClock() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const timeEl = document.getElementById('screen-clock');
    const dateEl = document.getElementById('screen-date');
    if (timeEl) timeEl.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    if (dateEl) {
      const week = ['日', '一', '二', '三', '四', '五', '六'][now.getDay()];
      dateEl.textContent = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} 周${week}`;
    }
  }

  function renderWaiting() {
    const scan = data.scan;
    ctx.host.innerHTML = `
      <div class="aurora aurora-a"></div>
      <div class="aurora aurora-b"></div>
      <div class="screen-root">
        <header class="screen-top glass corner-frame sc-enter-top d1">
          <div class="st-brand">
            <div class="brand-mark">${icon('scanframe', 20)}</div>
            <div>
              <div class="brand-cn glow-text">安龄智境</div>
              <div class="brand-en">ANJING VISION</div>
            </div>
          </div>
          <div class="st-divider"></div>
          <div class="st-room">
            <div class="r-name">房间 #${scan.id}</div>
            <div class="r-sub">数据准备中</div>
          </div>
          <div class="st-spacer"></div>
          <div class="st-clock"><div class="time" id="screen-clock">--:--:--</div><div class="date" id="screen-date">----</div></div>
          <button class="icon-btn" id="btn-screen-exit" data-tip="返回工作台 (Esc)" aria-label="返回">${icon('x', 17)}</button>
        </header>
        <div style="flex:1;display:grid;place-items:center">
          <div class="glass panel" style="width:560px;text-align:center;padding:48px">
            <div class="float-slow" style="color:var(--accent-2);margin-bottom:18px">${icon('cpu', 44)}</div>
            <h2 style="font-size:20px;font-weight:700">大屏数据尚未就绪</h2>
            <p class="muted" style="margin-top:10px;font-size:13.5px">
              当前房间状态：${esc(SCAN_STAGE_LABEL[scan.status] || scan.status)}（${scan.progress || 0}%）<br>
              完成后大屏将自动进入演示。
            </p>
            <div class="progress" style="width:340px;margin:22px auto 0"><div class="p-fill" style="width:${Math.max(4, Math.min(100, scan.progress || 0))}%"></div></div>
            <button class="btn" style="margin-top:24px" id="btn-screen-back">${icon('arrowRight', 15)} 返回房间工作台</button>
          </div>
        </div>
      </div>`;
    document.getElementById('btn-screen-exit')?.addEventListener('click', exit);
    document.getElementById('btn-screen-back')?.addEventListener('click', exit);
    clockTimer = setInterval(() => {
      const now = new Date();
      const pad = (n) => String(n).padStart(2, '0');
      const timeEl = document.getElementById('screen-clock');
      const dateEl = document.getElementById('screen-date');
      if (timeEl) timeEl.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
      if (dateEl) dateEl.textContent = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    }, 1000);
    pollTimer = setInterval(async () => {
      const scanNow = await api.getScan(scanId).catch(() => null);
      if (scanNow?.status === 'done') {
        clearInterval(pollTimer);
        data = await loadRoomData(scanId);
        render();
      }
    }, 5000);
  }
}

void typeName; void RISK_TYPE_LABEL;
