/* 安龄智境 · 轻量 SVG 图表：评分环 / 分类条 / 迷你折线 */

import { countUp, fmtScore, scoreTone } from './ui.js';

let uidSeed = 0;

export function scoreRingHTML({
  score,
  size = 168,
  stroke = 10,
  label = '综合评分',
  valueClass = '',
} = {}) {
  const uid = `ring-${++uidSeed}`;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const hasValue = score !== null && score !== undefined && !Number.isNaN(Number(score));
  const value = hasValue ? Math.max(0, Math.min(100, Number(score))) : null;
  const tone = scoreTone(value);
  const stops = {
    ok: ['#2FBF6B', '#9BF6B8'],
    warn: ['#E09600', '#FFD166'],
    bad: ['#E23A52', '#FF8FA3'],
    unknown: ['#3A4665', '#5A6B93'],
  }[tone] || ['#4F9CF9', '#7DD3FC'];
  const glow = { ok: 'rgba(92,233,138,0.55)', warn: 'rgba(255,183,3,0.5)', bad: 'rgba(255,90,110,0.55)', unknown: 'rgba(90,107,147,0.4)' }[tone];
  return `
  <div class="score-ring" data-ring data-score="${value ?? ''}" style="width:${size}px;height:${size}px">
    <svg width="${size}" height="${size}" aria-hidden="true">
      <defs>
        <linearGradient id="${uid}" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="${stops[0]}"/>
          <stop offset="1" stop-color="${stops[1]}"/>
        </linearGradient>
      </defs>
      <circle class="ring-track" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="${stroke}"/>
      <circle class="ring-arc" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none"
        stroke="url(#${uid})" stroke-width="${stroke}"
        stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${c.toFixed(2)}"
        style="--ring-glow:${glow}"/>
    </svg>
    <div class="ring-core">
      <div class="ring-score ${valueClass}" style="font-size:${Math.round(size * 0.24)}px">—</div>
      <div class="ring-label">${label}</div>
    </div>
  </div>`;
}

export function mountRings(root = document) {
  root.querySelectorAll('[data-ring]').forEach((el) => {
    if (el.dataset.mounted) return;
    el.dataset.mounted = '1';
    const raw = el.dataset.score;
    const hasValue = raw !== '' && raw !== 'null' && raw !== undefined;
    const value = hasValue ? Number(raw) : null;
    const arc = el.querySelector('.ring-arc');
    const scoreEl = el.querySelector('.ring-score');
    const c = Number(arc.getAttribute('stroke-dasharray'));
    requestAnimationFrame(() => {
      arc.style.strokeDashoffset = String(value === null ? c : c * (1 - value / 100));
    });
    if (value === null) {
      scoreEl.textContent = '—';
    } else {
      countUp(scoreEl, value, { duration: 1200, decimals: Number.isInteger(value) ? 0 : 1 });
    }
  });
}

export function barRowHTML({ name, weight = '', value, max = 100, tone }) {
  const hasValue = value !== null && value !== undefined && !Number.isNaN(Number(value));
  const v = hasValue ? Math.max(0, Math.min(max, Number(value))) : null;
  const cls = tone || (v === null ? '' : v >= 80 ? '' : v >= 60 ? 'warn' : 'bad');
  return `
  <div class="bar-row">
    <div class="bar-meta">
      <span class="bar-name">${name}</span>
      ${weight ? `<span class="bar-weight">权重 ${weight}</span>` : ''}
      <span class="bar-value">${v === null ? '—' : fmtScore(v)}</span>
    </div>
    <div class="bar-track"><div class="bar-fill ${cls}" data-width="${v === null ? 0 : v}"></div></div>
  </div>`;
}

export function mountBars(root = document) {
  root.querySelectorAll('.bar-fill[data-width]').forEach((el) => {
    if (el.dataset.mounted) return;
    el.dataset.mounted = '1';
    const width = Number(el.dataset.width || 0);
    requestAnimationFrame(() => { el.style.width = `${Math.max(0, Math.min(100, width))}%`; });
  });
}

export function sparklineHTML(values, { w = 96, h = 28, color = '#7DD3FC' } = {}) {
  const nums = (values || []).map(Number).filter((n) => Number.isFinite(n));
  if (nums.length < 2) return '';
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min || 1;
  const stepX = w / (nums.length - 1);
  const points = nums.map((n, i) => [i * stepX, h - 3 - ((n - min) / span) * (h - 8)]);
  const d = points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const last = points[points.length - 1];
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" fill="none" aria-hidden="true">
    <path d="${d}" stroke="${color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>
    <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.4" fill="${color}"/>
  </svg>`;
}
