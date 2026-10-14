/* 项目详情：房间/扫描管理 + 视频上传 + 管道进度 */

import { api } from '../api.js?v=house-scan-1';
import { icon } from '../icons.js';
import {
  esc, toast, fmtScore, timeAgo, scoreTone, emptyState, openModal,
  SCAN_STAGE_LABEL, scanStageIndex, SCAN_STAGES,
} from '../ui.js';
import { mountRings } from '../charts.js';
import { navigate, setLastScan } from '../app.js';

const ACTIVE_STATUSES = new Set(['uploading', 'extracting', 'reconstructing', 'cleaning', 'understanding']);

export async function projectView(ctx, projectId) {
  let project = null;
  let scans = [];
  const reportScores = new Map();
  let pollTimer = null;

  ctx.actions.innerHTML = '';
  const refreshBtn = document.createElement('button');
  refreshBtn.className = 'icon-btn';
  refreshBtn.dataset.tip = '刷新';
  refreshBtn.setAttribute('aria-label', '刷新');
  refreshBtn.innerHTML = icon('refresh', 17);
  refreshBtn.addEventListener('click', () => loadAll(true));
  ctx.actions.appendChild(refreshBtn);

  const newScanBtn = document.createElement('button');
  newScanBtn.className = 'btn btn-primary';
  newScanBtn.innerHTML = `${icon('plus', 16)} 新建房间`;
  newScanBtn.addEventListener('click', () => createRoom(ctx, projectId, refresh));
  ctx.actions.appendChild(newScanBtn);

  const importHouseBtn = document.createElement('button');
  importHouseBtn.className = 'btn';
  importHouseBtn.innerHTML = `${icon('home', 16)} 导入整屋演示`;
  importHouseBtn.addEventListener('click', async () => {
    importHouseBtn.disabled = true;
    importHouseBtn.textContent = '正在导入整屋点云…';
    try {
      const scan = await api.importHouseDemo(projectId);
      setLastScan(scan.id, 'house_demo');
      toast('ok', '整屋 Scan 已就绪', '五个空间已按门连接');
      navigate(`#/house/${scan.id}`);
    } catch (error) {
      toast('error', '整屋导入失败', error?.message || '');
      importHouseBtn.disabled = false;
      importHouseBtn.innerHTML = `${icon('home', 16)} 导入整屋演示`;
    }
  });
  ctx.actions.appendChild(importHouseBtn);

  ctx.host.innerHTML = `<div class="glass panel" style="height:100%">${'<div class="skeleton-line shimmer" style="width:40%"></div><div class="skeleton-line shimmer" style="width:70%"></div><div class="skeleton-block shimmer" style="flex:1;margin-top:12px"></div>'}</div>`;

  ctx.cleanup(() => clearInterval(pollTimer));

  await loadAll();
  pollTimer = setInterval(async () => {
    if (!scans.some((scan) => ACTIVE_STATUSES.has(scan.status))) return;
    await refresh();
  }, 5000);

  async function loadAll(showToast = false) {
    project = await api.getProject(projectId);
    ctx.title([{ label: '项目工作台', href: '#/projects' }, { label: project.name, current: true }]);
    await refresh();
    if (showToast) toast('ok', '已刷新', '数据已同步到最新状态');
  }

  async function refresh() {
    scans = await api.listScans(projectId);
    await Promise.all(scans.filter((scan) => scan.status === 'done' && scan.capture_type !== 'house_demo' && !reportScores.has(scan.id)).map(async (scan) => {
      try { reportScores.set(scan.id, (await api.getReport(scan.id))?.score ?? null); }
      catch { reportScores.set(scan.id, null); }
    }));
    render();
  }

  function render() {
    const doneCount = scans.filter((scan) => scan.status === 'done').length;
    const activeCount = scans.filter((scan) => ACTIVE_STATUSES.has(scan.status)).length;
    const scores = scans.map((scan) => reportScores.get(scan.id)).filter((score) => typeof score === 'number');
    const avg = scores.length ? (scores.reduce((sum, value) => sum + value, 0) / scores.length) : null;
    ctx.host.innerHTML = `
      <div class="view-project page">
        <div class="project-hero glass rise">
          <div class="ph-avatar">${esc(project.name.slice(0, 1))}</div>
          <div>
            <h1>${esc(project.name)}</h1>
            <div class="ph-sub">${icon('building', 13)} ${esc(project.address || '未填写地址')} · 项目编号 P${String(project.id).padStart(3, '0')}</div>
          </div>
          <div class="ph-stats">
            <div class="ph-stat"><div class="ps-value">${scans.length}</div><div class="ps-name">扫描</div></div>
            <div class="ph-stat"><div class="ps-value">${doneCount}</div><div class="ps-name">已完成</div></div>
            <div class="ph-stat"><div class="ps-value">${activeCount}</div><div class="ps-name">处理中</div></div>
            <div class="ph-stat"><div class="ps-value" style="color:${avg === null ? 'var(--text-3)' : `var(--${scoreTone(avg) === 'ok' ? 'ok' : scoreTone(avg) === 'warn' ? 'warn' : 'bad'})`}">${avg === null ? '—' : fmtScore(avg)}</div><div class="ps-name">平均评分</div></div>
          </div>
        </div>
        <div class="scan-list view-scroll" id="scan-list"></div>
      </div>`;

    const list = ctx.host.querySelector('#scan-list');
    if (!scans.length) {
      list.innerHTML = `<div class="glass panel">${emptyState({
        iconName: 'video',
        title: '还没有房间',
        desc: '新建房间并上传一段手机拍摄的房间视频，系统将自动完成三维重建、结构识别与风险评估。',
        action: '<button class="btn btn-primary" id="btn-empty-scan">新建房间</button>',
      })}</div>`;
      list.querySelector('#btn-empty-scan')?.addEventListener('click', () => createRoom(ctx, projectId, refresh));
      return;
    }

    list.innerHTML = scans.map((scan, index) => scanRow(scan, index)).join('');
    list.querySelectorAll('.scan-row').forEach((row) => {
      const scanId = Number(row.dataset.id);
      row.addEventListener('click', (event) => {
        if (event.target.closest('button')) return;
        const isHouse = scans.find((scan) => scan.id === scanId)?.capture_type === 'house_demo';
        setLastScan(scanId, isHouse ? 'house_demo' : 'video');
        navigate(`${isHouse ? '#/house' : '#/scans'}/${scanId}`);
      });
      row.addEventListener('keydown', (event) => {
        if (event.target !== row || !['Enter', ' '].includes(event.key)) return;
        event.preventDefault();
        const isHouse = scans.find((scan) => scan.id === scanId)?.capture_type === 'house_demo';
        setLastScan(scanId, isHouse ? 'house_demo' : 'video');
        navigate(`${isHouse ? '#/house' : '#/scans'}/${scanId}`);
      });
    });
    list.querySelectorAll('[data-action="open"]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const scanId = Number(btn.dataset.id);
        const isHouse = scans.find((scan) => scan.id === scanId)?.capture_type === 'house_demo';
        setLastScan(scanId, isHouse ? 'house_demo' : 'video');
        navigate(`${isHouse ? '#/house' : '#/scans'}/${scanId}`);
      });
    });
    list.querySelectorAll('[data-action="screen"]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const scanId = Number(btn.dataset.id);
        setLastScan(scanId);
        navigate(`#/screen/${scanId}`);
      });
    });
    list.querySelectorAll('[data-action="upload"]').forEach((btn) => {
      btn.addEventListener('click', () => openUploadModal(Number(btn.dataset.id), refresh));
    });
    list.querySelectorAll('[data-action="pdf"]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const scanId = Number(btn.dataset.id);
        try {
          const url = await api.reportPdf(scanId);
          window.open(url, '_blank', 'noopener');
        } catch (error) {
          toast('error', '报告下载失败', error?.message || '');
        }
      });
    });
    mountRings(ctx.host);
  }

  function scanRow(scan, index) {
    const house = scan.capture_type === 'house_demo';
    const active = ACTIVE_STATUSES.has(scan.status);
    const done = scan.status === 'done';
    const failed = scan.status === 'failed';
    const score = reportScores.get(scan.id);
    const stage = SCAN_STAGE_LABEL[scan.status] || scan.status;
    const progress = Math.max(0, Math.min(100, Number(scan.progress || 0)));
    return `
      <div class="scan-row glass rise" style="--i:${Math.min(index, 8)}" data-id="${scan.id}" role="button" tabindex="0">
        <div class="scan-thumb">${icon(done ? 'home' : failed ? 'alert' : 'video', 24)}</div>
        <div class="grow" style="min-width:0">
          <div class="scan-name">
            ${house ? '整屋演示' : `房间 ${index + 1}`}
            <span class="faint mono" style="font-size:12px">#${scan.id}</span>
            ${done ? '<span class="badge ok"><span class="dot"></span>已完成</span>'
              : failed ? '<span class="badge bad"><span class="dot"></span>失败</span>'
              : `<span class="badge info"><span class="dot"></span>${esc(stage)}</span>`}
          </div>
          <div class="scan-sub">
            <span>${icon(house ? 'home' : 'video', 12)} ${house ? '合成家庭点云' : '视频扫描'}</span>
            ${scan.created_at ? `<span>${esc(timeAgo(scan.created_at))}</span>` : ''}
            ${scan.message ? `<span>${esc(scan.message)}</span>` : ''}
            ${done && !house ? '<span>评估报告已生成</span>' : ''}
          </div>
        </div>
        <div class="scan-progress">
          ${active || failed ? `
            <div class="sp-meta"><span>${esc(stage)}</span><span class="num">${progress}%</span></div>
            <div class="progress thin"><div class="p-fill" style="width:${failed ? 100 : progress}%;${failed ? 'background:linear-gradient(90deg,#e23a52,#ff8fa3);box-shadow:none' : ''}"></div></div>
          ` : `
            <div class="sp-meta"><span>${house ? '空间拼接完成' : '评估完成'}</span><span class="num">100%</span></div>
            <div class="progress thin"><div class="p-fill" style="width:100%"></div></div>
          `}
        </div>
        <div>
          ${done ? house ? '<span class="badge info">整屋模型 · 未评分</span>' : `<span class="badge ${scoreTone(score) === 'ok' ? 'ok' : scoreTone(score) === 'warn' ? 'warn' : scoreTone(score) === 'bad' ? 'bad' : ''}">综合评分 ${score === null || score === undefined ? '—' : fmtScore(score)}</span>` : `<span class="faint" style="font-size:12px">${scan.status === 'uploading' ? '等待视频上传' : '处理中…'}</span>`}
        </div>
        <div class="scan-actions">
          ${scan.status === 'uploading' ? `<button class="btn btn-sm btn-primary" data-action="upload" data-id="${scan.id}">${icon('upload', 14)} 上传视频</button>` : ''}
          ${failed && !house ? `<button class="btn btn-sm" data-action="upload" data-id="${scan.id}">${icon('refresh', 14)} 重新上传</button>` : ''}
          ${done ? `
            <button class="btn btn-sm btn-primary" data-action="open" data-id="${scan.id}">${icon('home', 14)} ${house ? '整屋工作台' : '房间工作台'}</button>
            ${house ? '' : `<button class="btn btn-sm" data-action="screen" data-id="${scan.id}">${icon('monitor', 14)} 大屏</button>
            <button class="icon-btn" data-action="pdf" data-id="${scan.id}" data-tip="报告 PDF" aria-label="报告 PDF">${icon('download', 16)}</button>`}
          ` : ''}
        </div>
      </div>`;
  }

  function createRoom(context, pid, refreshFn) {
    (async () => {
      try {
        const scan = await api.createScan(pid);
        setLastScan(scan.id);
        openUploadModal(scan.id, refreshFn);
      } catch (error) {
        toast('error', '创建房间失败', error?.message || '');
      }
    })();
    void context;
  }
}

export function openUploadModal(scanId, onDone, presetFile = null) {
  const modal = openModal({
    title: '上传房间视频',
    desc: '视频将依次进入：抽帧 → SLAM3R 稠密重建 → 点云后处理 → SpatialLM 结构识别。',
    body: `
      <div class="col gap-4">
        <div class="dropzone" id="upload-dropzone" tabindex="0" role="button">
          <div class="dz-icon">${icon('upload', 30)}</div>
          <div class="dz-title" id="dz-title">点击选择视频，或拖拽到此处</div>
          <div class="dz-sub">支持 MP4 / MOV / AVI · 单个文件不超过 512 MB</div>
        </div>
        <input type="file" id="upload-file" accept="video/*,.mp4,.mov,.avi,.mkv" class="hidden">
        <details>
          <summary style="cursor:pointer;font-size:12.5px;color:var(--text-2)">参考尺寸标定（可选，强烈建议）</summary>
          <div class="muted" style="font-size:12px;margin:8px 0 12px;line-height:1.6">
            填入房间内已知物体的真实尺寸，系统将按它把重建恢复为真实比例，测量与评分更准确。
          </div>
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">
            <div class="field"><label>门宽度（m）</label><input class="input" id="ref-door-width" type="number" min="0.2" max="20" step="0.01" placeholder="0.85"></div>
            <div class="field"><label>门高度（m）</label><input class="input" id="ref-door-height" type="number" min="0.2" max="20" step="0.01" placeholder="2.00"></div>
            <div class="field"><label>床长度（m）</label><input class="input" id="ref-bed-length" type="number" min="0.2" max="20" step="0.01" placeholder="1.90"></div>
          </div>
        </details>
        <div id="upload-progress-wrap" class="hidden">
          <div class="row" style="justify-content:space-between;font-size:12.5px;color:var(--text-2);margin-bottom:7px">
            <span id="upload-phase">上传中</span><span class="num" id="upload-percent">0%</span>
          </div>
          <div class="progress"><div class="p-fill" id="upload-fill"></div></div>
        </div>
      </div>`,
    actions: [
      { label: '取消', variant: 'btn-ghost', onClick: (c) => c() },
      {
        label: '开始上传',
        variant: 'btn-primary',
        onClick: async (c, modalEl) => {
          const file = modalEl._file;
          if (!file) { toast('error', '请先选择视频文件'); return; }
          if (file.size > 512 * 1024 * 1024) { toast('error', '文件过大', '单个视频不能超过 512 MB'); return; }
          const refs = [];
          const pushRef = (selector, objectType, dimension) => {
            const value = Number(modalEl.querySelector(selector)?.value);
            if (Number.isFinite(value) && value > 0.1) refs.push({ object_type: objectType, dimension, meters: value });
          };
          pushRef('#ref-door-width', 'door', 'width');
          pushRef('#ref-door-height', 'door', 'height');
          pushRef('#ref-bed-length', 'bed', 'length');

          const progressWrap = modalEl.querySelector('#upload-progress-wrap');
          const fill = modalEl.querySelector('#upload-fill');
          const percent = modalEl.querySelector('#upload-percent');
          const phase = modalEl.querySelector('#upload-phase');
          progressWrap.classList.remove('hidden');
          const confirmBtn = modalEl.querySelector('.modal-actions .btn-primary');
          confirmBtn.classList.add('is-loading');
          try {
            if (refs.length) {
              phase.textContent = '保存参考尺寸';
              await api.putReferences(scanId, refs);
            }
            phase.textContent = '上传视频';
            await api.uploadVideo(scanId, file, (frac) => {
              const value = Math.round(frac * 100);
              fill.style.width = `${value}%`;
              percent.textContent = `${value}%`;
            });
            fill.style.width = '100%';
            percent.textContent = '100%';
            phase.textContent = '上传完成，管道已启动';
            toast('ok', '视频已上传', '三维重建管道已开始运行');
            setTimeout(() => {
              c();
              onDone?.();
            }, 600);
          } catch (error) {
            confirmBtn.classList.remove('is-loading');
            toast('error', '上传失败', error?.message || '');
          }
        },
      },
    ],
  });
  const modalEl = modal.modal;
  const dropzone = modalEl.querySelector('#upload-dropzone');
  const input = modalEl.querySelector('#upload-file');
  const title = modalEl.querySelector('#dz-title');

  const setFile = (file) => {
    modalEl._file = file;
    if (file) {
      const mb = (file.size / 1024 / 1024).toFixed(1);
      title.textContent = `${file.name}（${mb} MB）`;
      dropzone.style.borderColor = 'rgba(125,211,252,0.6)';
    }
  };
  if (presetFile) setFile(presetFile);

  dropzone.addEventListener('click', () => input.click());
  dropzone.addEventListener('keydown', (event) => { if (event.key === 'Enter') input.click(); });
  input.addEventListener('change', () => setFile(input.files?.[0]));
  ['dragenter', 'dragover'].forEach((type) => dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.add('dragover');
  }));
  ['dragleave', 'drop'].forEach((type) => dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.remove('dragover');
  }));
  dropzone.addEventListener('drop', (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) setFile(file);
  });
  void SCAN_STAGES; void scanStageIndex;
}
