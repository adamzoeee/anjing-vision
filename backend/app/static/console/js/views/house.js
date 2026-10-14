/* 整屋 Scan：五个空间共享坐标系，门连接图与整体点云/框架同步展示。 */
import { api } from '../api.js?v=house-scan-1';
import { icon } from '../icons.js';
import { esc, toast } from '../ui.js';
import { createViewer } from '../viewer/pointcloud.js?v=house-overlay-4';
import { setLastScan } from '../app.js';

const ROOM_COLORS = ['#ffb98d', '#8fcaf0', '#a2dfb0', '#c8b6f2', '#d8c7a8'];

function boundsForRoom(room) {
  return {
    min: room.origin,
    max: room.origin.map((value, index) => value + room.size[index]),
  };
}

function boundsForDoor(connection) {
  const [x, y] = connection.center;
  return { min: [x - 2.3, y - 2.3, 0], max: [x + 2.3, y + 2.3, 3] };
}

export async function houseView(ctx, scanId, selectedScan = null) {
  const scan = selectedScan || await api.getScan(scanId);
  if (scan.capture_type !== 'house_demo') {
    ctx.host.innerHTML = '<div class="glass panel"><h1>这不是整屋 Scan</h1></div>';
    return;
  }
  setLastScan(scanId, 'house_demo');
  ctx.title([
    { label: '项目工作台', href: '#/projects' },
    { label: '整屋 Scan', current: true },
  ]);
  ctx.actions.innerHTML = '';
  ctx.host.innerHTML = '<div class="glass panel"><div class="skeleton-line shimmer" style="width:36%"></div><div class="skeleton-block shimmer" style="height:62vh;margin-top:16px"></div></div>';

  let graph, project;
  try {
    [graph, project] = await Promise.all([
      api.getHouseConnections(scanId),
      api.getProject(scan.project_id),
    ]);
  } catch (error) {
    ctx.host.innerHTML = `<div class="glass panel"><h1>整屋数据暂不可用</h1><p class="muted">${esc(error?.message || '请检查导入结果')}</p></div>`;
    return;
  }
  ctx.title([
    { label: '项目工作台', href: '#/projects' },
    { label: project.name, href: `#/projects/${project.id}` },
    { label: `整屋 Scan #${scanId}`, current: true },
  ]);
  const labels = new Map(graph.rooms.map((room) => [room.id, room.label]));
  const bounds = graph.bounds_m;
  const size = bounds.max.map((value, index) => value - bounds.min[index]);
  ctx.host.innerHTML = `
    <div class="house-view">
      <header class="house-heading rise">
        <div>
          <div class="row gap-3"><h1>整屋点云与空间框架</h1><span class="badge ok"><span class="dot"></span>已连接</span></div>
          <p>合成家庭演示 · ${graph.rooms.length} 个空间通过 ${graph.connections.length} 组门连接 · Scan #${scanId}</p>
        </div>
        <div class="house-summary">
          <span><strong>${graph.rooms.length}</strong> 空间</span>
          <span><strong>${graph.connections.length}</strong> 组门</span>
          <span><strong>${size[0].toFixed(1)} × ${size[1].toFixed(1)}</strong> m 占地</span>
        </div>
      </header>
      <div class="house-content">
        <aside class="house-sidebar view-scroll" aria-label="整屋空间与门连接">
          <section class="glass panel house-panel">
            <div class="house-panel-title"><h2>空间拓扑</h2><span>点击定位</span></div>
            <div class="house-room-list">
              ${graph.rooms.map((room, index) => `
                <button class="house-room" data-room="${esc(room.id)}" type="button" aria-label="定位${esc(room.label)}">
                  <span class="house-room-mark" style="--room-color:${ROOM_COLORS[index % ROOM_COLORS.length]}"></span>
                  <span class="grow"><strong>${esc(room.label)}</strong><small>${room.size[0].toFixed(1)} × ${room.size[1].toFixed(1)} m</small></span>
                  <span class="house-room-doors">${room.door_ids.length} 扇门</span>
                </button>`).join('')}
            </div>
          </section>
          <section class="glass panel house-panel">
            <div class="house-panel-title"><h2>门对应关系</h2><span>双侧共用门洞</span></div>
            <div class="house-connection-list">
              ${graph.connections.map((link) => `
                <button class="house-connection" data-connection="${esc(link.id)}" type="button"
                  aria-label="定位${esc(labels.get(link.rooms[0]))}与${esc(labels.get(link.rooms[1]))}之间的门">
                  <span class="house-door-id">${esc(link.id)}</span>
                  <span class="grow"><strong>${esc(labels.get(link.rooms[0]))} ↔ ${esc(labels.get(link.rooms[1]))}</strong>
                    <small>${esc(link.doors[0].id)} ↔ ${esc(link.doors[1].id)}</small></span>
                  <span class="house-door-width">${link.width_m.toFixed(2)} m</span>
                </button>`).join('')}
            </div>
          </section>
          <p class="house-provenance">本 Scan 来自仓库内的合成点云与布局，门配对经过共墙、位置和方向校验。这里不显示正式风险评分。</p>
        </aside>
        <div class="room-stage house-stage corner-frame" id="house-stage">
          <div id="house-stage-host"></div>
          <div class="house-stage-head">
            <div class="house-mode-switch" role="group" aria-label="整屋显示模式">
              <button type="button" data-mode="scene" aria-pressed="false">点云</button>
              <button type="button" data-mode="structure" aria-pressed="false">框架</button>
              <button type="button" data-mode="overlay" class="active" aria-pressed="true">点云 + 框架</button>
            </div>
            <div class="house-stage-actions">
              <button type="button" class="icon-btn" id="house-reset" data-tip="查看整屋" aria-label="查看整屋">${icon('target', 17)}</button>
              <button type="button" class="icon-btn" id="house-shot" data-tip="保存截图" aria-label="保存截图">${icon('camera', 17)}</button>
            </div>
          </div>
          <div class="house-layer-bar" role="group" aria-label="框架图层">
            ${[['walls', '墙'], ['doors', '门'], ['windows', '窗'], ['objects', '家具'], ['labels', '标签']].map(([key, label]) =>
              `<button type="button" class="house-layer ${key === 'labels' ? '' : 'active'}" data-layer="${key}" aria-pressed="${key !== 'labels'}">${label}</button>`).join('')}
          </div>
          <div class="house-selection glass" id="house-selection" aria-live="polite" hidden></div>
          <div class="house-stage-foot glass"><span id="house-view-status">准备加载整屋点云…</span><span id="house-points">—</span></div>
          <div class="stage-overlay" id="house-overlay"><div class="stage-loading-card">
            <div class="spin" style="color:var(--accent)">${icon('radar', 34)}</div>
            <div class="load-title">载入整屋场景</div><div class="progress" style="width:260px"><div class="p-fill" id="house-load-fill"></div></div>
            <div class="load-sub" id="house-load-sub">正在读取点云与结构框架</div>
          </div></div>
        </div>
      </div>
    </div>`;

  const overlay = ctx.host.querySelector('#house-overlay');
  const status = ctx.host.querySelector('#house-view-status');
  const fill = ctx.host.querySelector('#house-load-fill');
  const detail = ctx.host.querySelector('#house-load-sub');
  const selection = ctx.host.querySelector('#house-selection');
  const viewer = createViewer(ctx.host.querySelector('#house-stage-host'), {
    onStatus: (event) => {
      if (event.phase === 'ready') {
        viewer.setMode('overlay');
        viewer.setLayer('labels', false);
        overlay.classList.add('leave');
        status.textContent = '整屋点云与框架已就绪';
      } else if (event.phase === 'error') {
        status.textContent = '场景加载失败';
        detail.textContent = event.message || '请刷新重试';
        overlay.querySelector('.load-title').textContent = '场景加载失败';
      } else {
        status.textContent = event.message || '正在加载';
        if (typeof event.progress === 'number') fill.style.width = `${Math.round(event.progress * 100)}%`;
      }
    },
    onStats: ({ points }) => { ctx.host.querySelector('#house-points').textContent = `${Number(points).toLocaleString()} 点`; },
  });
  ctx.cleanup(() => viewer.dispose());
  void viewer.load(scanId);

  const selectMode = (mode) => {
    viewer.setMode(mode);
    ctx.host.querySelectorAll('[data-mode]').forEach((item) => {
      const selected = item.dataset.mode === mode;
      item.classList.toggle('active', selected);
      item.setAttribute('aria-pressed', String(selected));
    });
  };
  ctx.host.querySelectorAll('[data-mode]').forEach((button) => button.addEventListener('click', () => selectMode(button.dataset.mode)));
  ctx.host.querySelectorAll('[data-layer]').forEach((button) => button.addEventListener('click', () => {
    const active = button.getAttribute('aria-pressed') !== 'true';
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
    viewer.setLayer(button.dataset.layer, active);
  }));
  ctx.host.querySelectorAll('[data-room]').forEach((button) => button.addEventListener('click', () => {
    const room = graph.rooms.find((item) => item.id === button.dataset.room);
    if (room) {
      viewer.highlightDoor(); viewer.focusBounds(boundsForRoom(room));
      selection.textContent = `${room.label} · ${room.size[0].toFixed(1)} × ${room.size[1].toFixed(1)} m · ${room.door_ids.length} 扇门`;
      selection.hidden = false;
    }
  }));
  ctx.host.querySelectorAll('[data-connection]').forEach((button) => button.addEventListener('click', () => {
    const link = graph.connections.find((item) => item.id === button.dataset.connection);
    if (link) {
      selectMode('structure'); viewer.highlightDoor(link.id); viewer.focusBounds(boundsForDoor(link));
      selection.textContent = `${link.id} · ${labels.get(link.rooms[0])}门 ↔ ${labels.get(link.rooms[1])}门 · 门洞 ${link.width_m.toFixed(2)} × ${link.height_m.toFixed(2)} m`;
      selection.hidden = false;
    }
  }));
  ctx.host.querySelector('#house-reset').addEventListener('click', () => { viewer.highlightDoor(); viewer.resetView(); selection.hidden = true; });
  ctx.host.querySelector('#house-shot').addEventListener('click', () => {
    const image = viewer.screenshot();
    if (!image) return;
    const link = document.createElement('a');
    link.href = image;
    link.download = `anjing_house_scan_${scanId}.png`;
    link.click();
    toast('ok', '整屋截图已保存', link.download);
  });
}
