/* 安龄智境 · 大屏工作台外壳 v2：主题 / 动效 / 悬浮 3D / 页面转场 / 路由 */

import { api, getSession, setSession, clearSession, isAdmin } from './api.js?v=house-flow-1';
import { icon } from './icons.js';
import { toast, esc } from './ui.js';
import { getBackground3D } from './background3d.js';
import { loginView } from './views/login.js';
import { projectsView } from './views/projects.js';
import { projectView } from './views/project.js?v=house-flow-1';
import { roomView } from './views/room.js?v=house-flow-1';
import { schemeView } from './views/scheme.js';
import { assistantView } from './views/assistant.js';
import { screenView } from './views/screen.js?v=house-flow-1';
import { usersView } from './views/users.js';

const root = document.getElementById('app');
const LAST_SCAN_KEY = 'anjing.console.lastScan';
const LAST_SCAN_TYPE_KEY = 'anjing.console.lastScanType';
const SKIN_KEY = 'anjing.console.skin';
const MOTION_KEY = 'anjing.console.motion';
const RAIL_KEY = 'anjing.console.rail';

let cleanups = [];
export function registerCleanup(fn) { cleanups.push(fn); }
function runCleanups() {
  cleanups.forEach((fn) => { try { fn(); } catch { /* ignore */ } });
  cleanups = [];
}

/* —— 主题 —— */
export function getSkin() {
  const value = localStorage.getItem(SKIN_KEY);
  return value === 'ivory' ? 'ivory' : 'dusk';
}
export function applySkin(skin = getSkin()) {
  document.documentElement.dataset.skin = skin;
  getBackground3D().applySkin();
}
export function setSkin(skin) {
  localStorage.setItem(SKIN_KEY, skin);
  applySkin(skin);
}

/* —— 动效强度（auto = 跟随系统；full = 全效，覆盖系统减少动效） —— */
export function getMotionMode() {
  return localStorage.getItem(MOTION_KEY) === 'full' ? 'full' : 'auto';
}
export function applyMotionMode(mode = getMotionMode()) {
  if (mode === 'full') document.documentElement.dataset.motion = 'full';
  else delete document.documentElement.dataset.motion;
}
export function setMotionMode(mode) {
  localStorage.setItem(MOTION_KEY, mode);
  applyMotionMode(mode);
}
export function motionEnabled() {
  if (getMotionMode() === 'full') return true;
  return !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/* —— 侧边栏折叠（工作台外壳；折叠态只保留图标，悬停气泡显示名称） —— */
export function getRailMode() {
  return localStorage.getItem(RAIL_KEY) === 'collapsed' ? 'collapsed' : 'expanded';
}
export function applyRail(mode = getRailMode()) {
  const collapsed = mode === 'collapsed';
  root.classList.toggle('rail-collapsed', collapsed);
  /* 展开态文字可见，不需要气泡；折叠态把名称挂成 data-tip（气泡改在右侧弹出）。 */
  root.querySelectorAll('[data-rail-label]').forEach((node) => {
    if (collapsed) node.setAttribute('data-tip', node.dataset.railLabel);
    else node.removeAttribute('data-tip');
  });
  const toggle = root.querySelector('#rail-toggle');
  if (toggle) {
    const label = collapsed ? '展开侧边栏' : '收起侧边栏';
    toggle.setAttribute('aria-label', label);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    /* 与其它导航按钮一致：折叠态（纯图标）才挂气泡，展开态文字已经写着动作 */
    if (collapsed) toggle.setAttribute('data-tip', label);
    else toggle.removeAttribute('data-tip');
    const text = toggle.querySelector('span');
    if (text) text.textContent = label;
  }
}
export function setRailMode(mode) {
  localStorage.setItem(RAIL_KEY, mode === 'collapsed' ? 'collapsed' : 'expanded');
  applyRail(mode);
}

/* —— 路由 —— */
export function navigate(hash, { replace = false } = {}) {
  if (location.hash === hash) { render(); return; }
  if (replace) {
    history.replaceState(null, '', hash);
    render();
  } else {
    location.hash = hash;
  }
}
export function setLastScan(id, type = 'video') {
  if (id) {
    localStorage.setItem(LAST_SCAN_KEY, String(id));
    localStorage.setItem(LAST_SCAN_TYPE_KEY, type);
  }
}
export function getLastScan() {
  return localStorage.getItem(LAST_SCAN_KEY) || '';
}
function lastScanIsHouse() { return ['house', 'house_demo'].includes(localStorage.getItem(LAST_SCAN_TYPE_KEY)); }
export function logout() {
  clearSession();
  runCleanups();
  navigate('#/login');
}

/* —— 页面转场光带 —— */
let sweepEl = null;
function playSweep() {
  if (!sweepEl) return;
  if (!motionEnabled()) return;
  sweepEl.classList.remove('run');
  void sweepEl.offsetWidth;
  sweepEl.classList.add('run');
}

/* —— 宽度门帘 —— */
function checkWidth() {
  const gate = document.getElementById('min-width-gate');
  gate.hidden = window.innerWidth >= 1280
    || location.hash.startsWith('#/projects')
    || location.hash.startsWith('#/scans/')
    || location.hash.startsWith('#/house/')
    || location.hash.startsWith('#/users');
  const iconHost = document.getElementById('gate-icon');
  if (!iconHost.dataset.filled) {
    iconHost.innerHTML = icon('monitor', 44);
    iconHost.dataset.filled = '1';
  }
}

/* —— 应用骨架 —— */
function buildShell() {
  const session = getSession() || {};
  const user = session.user || {};
  const admin = isAdmin();
  const currentHouse = /^#\/house\/(\d+)/.exec(location.hash);
  const lastScan = currentHouse ? currentHouse[1] : getLastScan();
  const houseScan = Boolean(currentHouse) || lastScanIsHouse();
  const hash = location.hash;
  const isActive = (prefix) => hash.startsWith(prefix);
  /* 「房间/整屋工作台」入口只在当前就在工作台或其下级页（如方案可视化）时出现 */
  const scanRoute = /^#\/(?:scans|house)\/(\d+)/.exec(hash);
  const showRoomNav = Boolean(scanRoute);
  const roomNavId = scanRoute ? scanRoute[1] : lastScan;
  const initial = esc((user.name || '用').slice(0, 1));
  const skin = getSkin();
  const motion = getMotionMode();

  root.innerHTML = `
    <div class="aurora aurora-a"></div>
    <div class="aurora aurora-b"></div>
    <aside class="rail">
      <div class="rail-brand">
        <div class="brand-mark">${icon('scanframe', 22)}</div>
        <div class="brand-text">
          <div class="brand-cn">安龄智境</div>
          <div class="brand-en">ANJING VISION</div>
        </div>
      </div>
      <button class="nav-item rail-toggle" id="rail-toggle" aria-label="收起侧边栏" aria-expanded="true">
        ${icon('chevronRight', 18)}<span>收起侧边栏</span>
      </button>
      <nav class="rail-nav">
        <div class="nav-section eyebrow">工作台</div>
        <a class="nav-item ${isActive('#/projects') ? 'active' : ''}" href="#/projects" data-rail-label="项目工作台">
          ${icon('folder', 18)}<span>项目工作台</span>
        </a>
        ${showRoomNav ? `
        <a class="nav-item ${isActive('#/scans') || isActive('#/house') ? 'active' : ''}" href="#/scans/${roomNavId}" data-rail-label="${houseScan ? '整屋工作台' : '房间工作台'}">
          ${icon('home', 18)}<span>${houseScan ? '整屋工作台' : '房间工作台'}</span>
        </a>` : ''}
        ${admin ? `
        <div class="nav-section eyebrow">机构</div>
        <a class="nav-item ${isActive('#/users') ? 'active' : ''}" href="#/users" data-rail-label="用户管理">
          ${icon('user', 18)}<span>用户管理</span>
        </a>` : ''}
      </nav>
      <div class="rail-tools">
        <div class="tool-row">
          <span class="tool-label">主题</span>
          <span class="skin-dots">
            <button class="skin-dot dusk ${skin === 'dusk' ? 'active' : ''}" data-skin="dusk" aria-label="暮光主题" data-tip="暮光"></button>
            <button class="skin-dot ivory ${skin === 'ivory' ? 'active' : ''}" data-skin="ivory" aria-label="象牙主题" data-tip="象牙"></button>
          </span>
        </div>
        <div class="tool-row">
          <span class="tool-label">全效动效</span>
          <button class="switch ${motion === 'full' ? 'on' : ''}" id="motion-switch" role="switch" aria-checked="${motion === 'full'}" aria-label="全效动效"></button>
        </div>
      </div>
      <div class="rail-user">
        <div class="avatar">${initial}</div>
        <div class="grow" style="min-width:0">
          <div class="u-name truncate">${esc(user.name || '未登录')}</div>
          <div class="u-org truncate">${esc(user.org_name || '')}${user.role ? ` · ${admin ? '管理员' : '成员'}` : ''}</div>
        </div>
        <button class="icon-btn" id="btn-logout" data-tip="退出登录" aria-label="退出登录">${icon('logout', 17)}</button>
      </div>
    </aside>
    <div class="main">
      <header class="topbar">
        <nav class="breadcrumb" id="breadcrumb" aria-label="面包屑"></nav>
        <div class="topbar-actions" id="topbar-actions"></div>
      </header>
      <section class="view" id="view-container"></section>
    </div>`;

  applyRail();   /* 外壳重建后同步折叠态与气泡 */
  root.querySelector('#rail-toggle')?.addEventListener('click', () => {
    setRailMode(getRailMode() === 'collapsed' ? 'expanded' : 'collapsed');
  });
  root.querySelector('#btn-logout').addEventListener('click', logout);
  root.querySelectorAll('.skin-dot').forEach((dot) => {
    dot.addEventListener('click', () => {
      setSkin(dot.dataset.skin);
      root.querySelectorAll('.skin-dot').forEach((node) => node.classList.toggle('active', node === dot));
    });
  });
  root.querySelector('#motion-switch')?.addEventListener('click', () => {
    const next = getMotionMode() === 'full' ? 'auto' : 'full';
    setMotionMode(next);
    const sw = root.querySelector('#motion-switch');
    sw.classList.toggle('on', next === 'full');
    sw.setAttribute('aria-checked', String(next === 'full'));
  });

  return {
    host: root.querySelector('#view-container'),
    breadcrumb: root.querySelector('#breadcrumb'),
    actions: root.querySelector('#topbar-actions'),
  };
}

function renderBreadcrumb(el, crumbs) {
  el.innerHTML = crumbs.map((crumb, index) => {
    const last = index === crumbs.length - 1;
    const node = last
      ? `<span class="crumb current">${esc(crumb.label)}</span>`
      : `<span class="crumb" data-href="${esc(crumb.href || '#/projects')}">${esc(crumb.label)}</span>`;
    return index === 0 ? node : `<span class="sep">/</span>${node}`;
  }).join('');
  el.querySelectorAll('.crumb[data-href]').forEach((node) => {
    node.addEventListener('click', () => navigate(node.dataset.href));
  });
}

const routes = [
  { re: /^#\/projects\/?$/, layout: 'shell', run: (ctx) => projectsView(ctx) },
  { re: /^#\/projects\/(\d+)\/?$/, layout: 'shell', run: (ctx, m) => projectView(ctx, Number(m[1])) },
  { re: /^#\/scans\/(\d+)\/?$/, layout: 'shell', run: (ctx, m) => roomView(ctx, Number(m[1])) },
  { re: /^#\/scans\/(\d+)\/assistant\/?$/, layout: 'shell', run: (ctx, m) => assistantView(ctx, Number(m[1])) },
  { re: /^#\/house\/(\d+)\/?$/, layout: 'shell', run: (_ctx, m) => navigate(`#/scans/${m[1]}`, { replace: true }) },
  { re: /^#\/scans\/(\d+)\/schemes\/(\d+)\/?$/, layout: 'shell', run: (ctx, m) => schemeView(ctx, Number(m[1]), Number(m[2])) },
  { re: /^#\/screen\/(\d+)\/?$/, layout: 'bare', run: (ctx, m) => screenView(ctx, Number(m[1])) },
  { re: /^#\/users\/?$/, layout: 'shell', run: (ctx) => (isAdmin() ? usersView(ctx) : navigate('#/projects', { replace: true })) },
  { re: /^#\/login\/?$/, layout: 'bare', run: (ctx) => loginView(ctx) },
];

function matchRoute(hash) {
  for (const route of routes) {
    const m = route.re.exec(hash || '');
    if (m) return { route, params: m };
  }
  return null;
}

async function render() {
  checkWidth();
  runCleanups();
  const hash = location.hash || '';
  root.classList.toggle('house-route', hash.startsWith('#/scans/') && lastScanIsHouse());
  root.classList.toggle('project-route', hash.startsWith('#/projects'));
  root.classList.toggle('assistant-route', /^#\/scans\/\d+\/assistant\/?$/.test(hash));
  const session = getSession();

  if (!session && hash !== '#/login') {
    navigate('#/login', { replace: true });
    return;
  }

  const matched = matchRoute(hash) || { route: routes[0], params: [] };
  if (!matched.route) return;
  const bare = matched.route.layout !== 'shell';
  const isImmersive = hash.startsWith('#/screen') || hash.startsWith('#/scans') || hash.startsWith('#/house');

  const mount = async () => {
    if (bare) {
      root.classList.add('bare');
      const ctx = {
        params: matched.params, host: root, title: () => {}, actions: null,
        cleanup: registerCleanup,
      };
      await matched.route.run(ctx, matched.params);
      return;
    }
    root.classList.remove('bare');
    const shell = buildShell();
    const ctx = {
      params: matched.params,
      host: shell.host,
      title: (crumbs) => renderBreadcrumb(shell.breadcrumb, crumbs),
      actions: shell.actions,
      cleanup: registerCleanup,
    };
    try {
      await matched.route.run(ctx, matched.params);
    } catch (error) {
      if (error?.status === 401) return;
      shell.host.innerHTML = `
        <div class="glass panel" style="align-items:center;justify-content:center;height:100%">
          <div class="empty-state">
            <div class="e-icon">${icon('alert', 44)}</div>
            <h3>页面加载失败</h3>
            <p>${esc(error?.message || String(error))}</p>
            <button class="btn" id="btn-retry-view">重新加载</button>
          </div>
        </div>`;
      shell.host.querySelector('#btn-retry-view')?.addEventListener('click', () => render());
      toast('error', '页面加载失败', error?.message || String(error));
    }
  };

  const bg = getBackground3D();
  /* 装饰层默认用设计好的温馨户型；只有进到具体 Scan 工作台才换成该 Scan 的真实布局 */
  bg.setScan(isImmersive ? getLastScan() : '');
  bg.setSubtle(isImmersive);
  bg.setMuted(hash.startsWith('#/login'));

  const transitionSupported = typeof document.startViewTransition === 'function' && motionEnabled();
  if (transitionSupported) {
    playSweep();
    const transition = document.startViewTransition(() => mount());
    transition.finished.catch(() => {});
  } else {
    await mount().catch((error) => {
      if (error?.status !== 401) toast('error', '页面加载失败', error?.message || String(error));
    });
    if (!bare) playSweep();
  }
}

async function boot() {
  applySkin();
  applyMotionMode();
  applyRail();
  sweepEl = document.createElement('div');
  sweepEl.id = 'route-sweep';
  document.body.appendChild(sweepEl);

  const params = new URLSearchParams(location.search);
  const urlToken = params.get('token');
  if (urlToken) {
    setSession({ ...(getSession() || {}), token: urlToken });
    const clean = location.pathname + (location.hash || '');
    history.replaceState(null, '', clean);
  }
  if (getSession() && !getSession().user) {
    try {
      const user = await api.me();
      setSession({ ...getSession(), user });
    } catch { /* 401 由事件处理 */ }
  }
  window.addEventListener('hashchange', () => render());
  window.addEventListener('resize', checkWidth);
  window.addEventListener('anjing:unauthorized', () => {
    toast('error', '登录已过期', '请重新登录继续使用');
    runCleanups();
    navigate('#/login', { replace: true });
  });
  window.addEventListener('beforeunload', runCleanups);
  render();
}

boot();
