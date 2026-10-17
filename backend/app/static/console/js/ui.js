/* 安龄智境 · UI 基础件：DOM / 格式化 / Toast / 模态 / 计数动画 */

import { icon } from './icons.js';

export function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* —— 格式化 —— */
export function fmtMeters(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `${Number(value).toFixed(digits)} m`;
}
export function fmtCm(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `${Math.round(Number(value) * 100)} cm`;
}
export function fmtScore(value) {
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}
export function fmtPct(value, digits = 0) {
  if (value === null || value === undefined) return '—';
  return `${Number(value).toFixed(digits)}%`;
}
export function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function timeAgo(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const diff = Date.now() - d.getTime();
  const minute = 60000;
  const hour = 3600000;
  const day = 86400000;
  if (diff < minute) return '刚刚';
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`;
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`;
  if (diff < day * 30) return `${Math.floor(diff / day)} 天前`;
  return fmtDateTime(iso).slice(0, 10);
}

export const RISK_LEVELS = {
  high: { label: '高风险', tone: 'bad', cls: 'level-high' },
  medium: { label: '中风险', tone: 'warn', cls: 'level-medium' },
  low: { label: '低风险', tone: 'ok', cls: 'level-low' },
  unknown: { label: '无法评估', tone: 'neutral', cls: 'level-unknown' },
};
export function riskLevel(level) {
  const key = String(level || 'unknown').toLowerCase();
  return RISK_LEVELS[key] || RISK_LEVELS.unknown;
}

export const SCAN_STAGES = ['uploading', 'extracting', 'reconstructing', 'cleaning', 'understanding', 'done'];
export const SCAN_STAGE_LABEL = {
  uploading: '上传中',
  awaiting_video: '等待上传新视频',
  uploaded: '视频已上传，等待开始',
  extracting: '视频抽帧',
  reconstructing: '三维重建',
  cleaning: '点云后处理',
  understanding: '空间结构识别',
  done: '已完成',
  failed: '失败',
};
export function scanStageIndex(status) {
  const idx = SCAN_STAGES.indexOf(status);
  return idx === -1 ? (status === 'done' ? SCAN_STAGES.length - 1 : 0) : idx;
}

export function scoreTone(score) {
  if (score === null || score === undefined) return 'unknown';
  const n = Number(score);
  if (n >= 80) return 'ok';
  if (n >= 60) return 'warn';
  return 'bad';
}

/* —— 家具/结构类型中文名 —— */
export const TYPE_CN = {
  bed: '床', table: '桌', desk: '书桌', dining_table: '餐桌',
  chair: '椅', sofa: '沙发', cabinet: '柜', wardrobe: '衣柜',
  nightstand: '床头柜', bookshelf: '书架', shelf: '置物架',
  tv: '电视', television: '电视', fridge: '冰箱', refrigerator: '冰箱',
  washing_machine: '洗衣机', air_conditioner: '空调',
  storage_rack: '置物架', storage: '储物柜', dresser: '斗柜',
  clothes_rack: '挂衣架', coat_rack: '衣帽架', shoe_cabinet: '鞋柜',
  coffee_table: '茶几', stool: '凳子', bench: '长凳',
  door: '门', window: '窗', wall: '墙', object: '家具',
};
export function typeName(type) {
  const key = String(type || '').toLowerCase();
  if (TYPE_CN[key]) return TYPE_CN[key];
  const KEYWORDS = [
    ['bed', '床'], ['nightstand', '床头柜'], ['table', '桌'], ['desk', '书桌'],
    ['bookcase', '书柜'], ['bookshelf', '书架'], ['shelf', '书架'],
    ['wardrobe', '衣柜'], ['sideboard', '边柜'], ['cabinet', '柜'],
    ['sofa', '沙发'], ['chair', '椅'], ['stool', '凳'], ['bench', '长凳'],
    ['fridge', '冰箱'], ['washing', '洗衣机'], ['television', '电视'],
    ['tv', '电视'], ['rack', '置物架'], ['mirror', '镜子'], ['plant', '绿植'],
  ];
  for (const [keyword, label] of KEYWORDS) {
    if (key.includes(keyword)) return label;
  }
  return type || '家具';
}

export function normalizeInstanceId(value) {
  return String(value || '').toLowerCase().replace(/_(\d+)$/, (_, digits) => `_${Number(digits)}`);
}

export function measuredSizeMap(measurements) {
  const map = new Map();
  (measurements?.objects || []).forEach((item) => {
    if (item.length_m != null && item.width_m != null && item.height_m != null) {
      map.set(normalizeInstanceId(item.id || item.instance_id), item);
    }
  });
  return map;
}

/* —— 指标数值格式化（按单位） —— */
export function formatMeasurement(value, unit) {
  if (value === null || value === undefined) return { text: '—', unit: '' };
  if (unit === 'boolean') return { text: value ? '达标' : '未达标', unit: '' };
  const n = Number(value);
  if (Number.isNaN(n)) return { text: String(value), unit: unit && unit !== 'ratio' ? unit : '' };
  if (unit === 'm') return { text: n.toFixed(2), unit: 'm' };
  if (unit === 'm²') return { text: n.toFixed(1), unit: 'm²' };
  if (unit === 'ratio') return { text: n.toFixed(2), unit: '' };
  if (unit === '°') return { text: n.toFixed(0), unit: '°' };
  return { text: n.toFixed(2).replace(/\.?0+$/, ''), unit: unit || '' };
}
export function metricTone(value, { good = 0.8, warn = 0.6, higherBetter = true } = {}) {
  if (value === null || value === undefined) return 'unknown';
  const n = Number(value);
  if (Number.isNaN(n)) return 'unknown';
  if (higherBetter) {
    if (n >= good) return 'ok';
    if (n >= warn) return 'warn';
    return 'bad';
  }
  if (n <= good) return 'ok';
  if (n <= warn) return 'warn';
  return 'bad';
}

/* —— 计数动画 —— */
const prefersReduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function countUp(el, to, { duration = 800, decimals = 0, suffix = '', prefix = '' } = {}) {
  if (!el) return;
  const target = Number(to);
  if (!Number.isFinite(target)) { el.textContent = '—'; return; }
  if (prefersReduced()) { el.textContent = `${prefix}${target.toFixed(decimals)}${suffix}`; return; }
  const from = 0;
  const start = performance.now();
  const ease = (t) => 1 - Math.pow(1 - t, 3);
  function frame(now) {
    const t = Math.min(1, (now - start) / duration);
    const value = from + (target - from) * ease(t);
    el.textContent = `${prefix}${value.toFixed(decimals)}${suffix}`;
    if (t < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

/* —— Toast —— */
let toastStack = null;
export function toast(type, title, message = '', { timeout = 4200 } = {}) {
  if (!toastStack) {
    toastStack = document.createElement('div');
    toastStack.className = 'toast-stack';
    document.getElementById('overlay-root').appendChild(toastStack);
  }
  const iconName = type === 'ok' ? 'checkCircle' : type === 'error' ? 'alert' : 'info';
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.setAttribute('role', type === 'error' ? 'alert' : 'status');
  node.innerHTML = `
    <div class="t-icon">${icon(iconName, 18)}</div>
    <div class="grow">
      <div class="t-title">${esc(title)}</div>
      ${message ? `<div class="t-msg">${esc(message)}</div>` : ''}
    </div>
    <button class="icon-btn t-close" aria-label="关闭通知">${icon('x', 15)}</button>`;
  const close = () => {
    node.classList.add('leaving');
    setTimeout(() => node.remove(), 240);
  };
  node.querySelector('.t-close').addEventListener('click', close);
  toastStack.appendChild(node);
  if (timeout) setTimeout(close, timeout);
  return close;
}

/* —— 模态框 —— */
export function openModal({ title, desc = '', body = '', actions = [], width, onClose } = {}) {
  const overlayRoot = document.getElementById('overlay-root');
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  if (width) backdrop.style.setProperty('--modal-width', width);
  backdrop.innerHTML = `
    <div class="modal glass" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="modal-title">${esc(title)}</div>
      ${desc ? `<div class="modal-desc">${esc(desc)}</div>` : ''}
      <div class="modal-body"></div>
      <div class="modal-actions"></div>
    </div>`;
  const modal = backdrop.querySelector('.modal');
  if (width) modal.style.width = width;
  const bodyEl = backdrop.querySelector('.modal-body');
  if (typeof body === 'string') bodyEl.innerHTML = body;
  else if (body instanceof Node) bodyEl.appendChild(body);
  const actionsEl = backdrop.querySelector('.modal-actions');
  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose();
  };
  actions.forEach((action) => {
    const btn = document.createElement('button');
    btn.className = `btn ${action.variant || ''}`;
    btn.textContent = action.label;
    btn.addEventListener('click', () => action.onClick?.(close, modal));
    actionsEl.appendChild(btn);
  });
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  backdrop.addEventListener('mousedown', (event) => {
    if (event.target === backdrop) close();
  });
  overlayRoot.appendChild(backdrop);
  const firstInput = modal.querySelector('input, textarea, select');
  if (firstInput) setTimeout(() => firstInput.focus(), 60);
  return { close, modal, body: bodyEl };
}

export function confirmDialog({ title, message, confirmText = '确认', danger = false }) {
  return new Promise((resolve) => {
    const { close } = openModal({
      title,
      body: `<div style="font-size:13.5px;color:var(--text-2);line-height:1.7">${esc(message)}</div>`,
      actions: [
        { label: '取消', variant: 'btn-ghost', onClick: (c) => { c(); resolve(false); } },
        { label: confirmText, variant: danger ? 'btn-danger' : 'btn-primary', onClick: (c) => { c(); resolve(true); } },
      ],
      onClose: () => resolve(false),
    });
    void close;
  });
}

/* —— 灯箱 —— */
export function lightbox(src, alt = '') {
  const overlayRoot = document.getElementById('overlay-root');
  const node = document.createElement('div');
  node.className = 'lightbox';
  node.innerHTML = `<img src="${src}" alt="${esc(alt)}">`;
  const close = () => { node.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  node.addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  overlayRoot.appendChild(node);
}

/* —— 骨架 —— */
export function skeletonLines(count = 3) {
  return Array.from({ length: count }, (_, i) =>
    `<div class="skeleton-line shimmer" style="width:${88 - i * 12}%"></div>`).join('');
}

export function emptyState({ iconName = 'folder', title = '暂无内容', desc = '', action = '' } = {}) {
  return `
    <div class="empty-state">
      <div class="e-icon">${icon(iconName, 44)}</div>
      <h3>${esc(title)}</h3>
      ${desc ? `<p>${esc(desc)}</p>` : ''}
      ${action}
    </div>`;
}

export function debounce(fn, wait = 200) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

/* —— 程序化蓝图封面（项目卡片） —— */
export function blueprintSVG(seed = 1, w = 400, h = 150) {
  let s = Math.max(1, Math.floor(seed)) * 2654435761 % 4294967296;
  const rand = () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296;
  };
  const W = 260, H = 90, ox = (w - W) / 2, oy = (h - H) / 2;
  const walls = [];
  const cols = 3 + Math.floor(rand() * 2);
  const rows = 2;
  // 外墙
  walls.push(`M${ox},${oy} H${ox + W} V${oy + H} H${ox} Z`);
  // 内墙分割
  let x = ox;
  for (let i = 0; i < cols - 1; i++) {
    x += (W / cols) * (0.72 + rand() * 0.5);
    const gap = 14 + rand() * 16;
    const gy = oy + (H / rows) * (0.3 + rand() * 0.4);
    walls.push(`M${x},${oy} V${gy - gap / 2}`);
    walls.push(`M${x},${gy + gap / 2} V${oy + H}`);
  }
  // 家具块
  const blocks = [];
  const count = 5 + Math.floor(rand() * 4);
  for (let i = 0; i < count; i++) {
    const bw = 14 + rand() * 30;
    const bh = 10 + rand() * 16;
    const bx = ox + 10 + rand() * (W - bw - 20);
    const by = oy + 8 + rand() * (H - bh - 16);
    blocks.push(`<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="2" style="fill:color-mix(in srgb, var(--accent-3) 7%, transparent);stroke:color-mix(in srgb, var(--accent-3) 24%, transparent);stroke-width:1"/>`);
  }
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
    <rect width="${w}" height="${h}" style="fill:color-mix(in srgb, var(--bg-2) 85%, transparent)"/>
    <g fill="none" style="stroke:color-mix(in srgb, var(--accent) 60%, transparent);stroke-width:1.6;stroke-linejoin:round">
      ${walls.map((d) => `<path d="${d}"/>`).join('')}
    </g>
    ${blocks.join('')}
    <circle cx="${(ox + W * (0.2 + rand() * 0.6)).toFixed(1)}" cy="${(oy + H * 0.5).toFixed(1)}" r="3" style="fill:var(--accent)" opacity="0.9">
      <animate attributeName="opacity" values="0.9;0.3;0.9" dur="3.2s" repeatCount="indefinite"/>
    </circle>
  </svg>`;
}
