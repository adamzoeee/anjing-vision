/* 登录页：品牌 3D 粒子视觉 + 玻璃登录卡 */

import { api, getSession, setSession } from '../api.js?v=house-scan-1';
import { icon } from '../icons.js';
import { toast, esc } from '../ui.js';

function startHeroCanvas(canvas) {
  const ctx = canvas.getContext('2d');
  let raf = 0;
  let width = 0;
  let height = 0;
  let dpr = 1;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const hexToRgba = (hex, alpha) => {
    const raw = (hex || '#ff8e6b').replace('#', '');
    const full = raw.length === 3 ? raw.split('').map((c) => c + c).join('') : raw;
    const value = parseInt(full, 16);
    return `rgba(${(value >> 16) & 255},${(value >> 8) & 255},${value & 255},${alpha})`;
  };
  let palette = { accent: '#ff8e6b', accent2: '#ffc9a3', accent3: '#a78bfa' };
  const readPalette = () => {
    const cs = getComputedStyle(document.documentElement);
    palette = {
      accent: cs.getPropertyValue('--accent').trim() || palette.accent,
      accent2: cs.getPropertyValue('--accent-2').trim() || palette.accent2,
      accent3: cs.getPropertyValue('--accent-3').trim() || palette.accent3,
    };
  };
  readPalette();

  const stars = Array.from({ length: 220 }, () => ({
    x: (Math.random() * 2 - 1) * 3.4,
    y: (Math.random() * 2 - 1) * 2.2,
    z: Math.random() * 2.6 + 0.5,
    r: Math.random() * 1.1 + 0.5,
  }));

  function resize() {
    const rect = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = rect.width;
    height = rect.height;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  resize();

  const roomSize = 1.5;
  const cube = [];
  for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) cube.push([x, y, z]);
  const edges = [];
  cube.forEach((a, i) => cube.forEach((b, j) => {
    if (j <= i) return;
    const diff = a.reduce((acc, v, k) => acc + Math.abs(v - b[k]), 0);
    if (diff === 2) edges.push([a, b]);
  }));

  const project = (p, rotY, rotX, cx, cy, scale) => {
    const [x, y, z] = p;
    const cosY = Math.cos(rotY), sinY = Math.sin(rotY);
    const x1 = x * cosY - z * sinY;
    const z1 = x * sinY + z * cosY;
    const cosX = Math.cos(rotX), sinX = Math.sin(rotX);
    const y1 = y * cosX - z1 * sinX;
    const z2 = y * sinX + z1 * cosX;
    const dist = 3.4;
    const f = scale / (dist + z2 * 0.7);
    return [cx + x1 * f, cy + y1 * f, f];
  };

  const t0 = performance.now();
  function draw(t) {
    ctx.clearRect(0, 0, width, height);
    const cx = width * 0.5;
    const cy = height * 0.52;
    const scale = Math.min(width, height) * 0.42;
    const rotY = t * 0.16 + 0.5;
    const rotX = 0.42 + Math.sin(t * 0.12) * 0.06;

    // 星尘
    stars.forEach((star) => {
      const p = project([star.x, star.y, star.z], rotY * 0.32, rotX * 0.4, cx, cy, scale);
      const depth = 1 - Math.min(1, (star.z - 0.5) / 2.6);
      const alpha = 0.1 + depth * 0.3;
      ctx.fillStyle = hexToRgba(palette.accent2, Number(alpha.toFixed(3)));
      ctx.beginPath();
      ctx.arc(p[0], p[1], star.r * (0.7 + depth * 0.9), 0, Math.PI * 2);
      ctx.fill();
    });

    // 地面网格
    ctx.strokeStyle = hexToRgba(palette.accent, 0.16);
    ctx.lineWidth = 1;
    const gridN = 6;
    for (let i = 0; i <= gridN; i++) {
      const v = -1 + (2 * i) / gridN;
      const a = project([v, 1, -1], rotY, rotX, cx, cy, scale);
      const b = project([v, 1, 1], rotY, rotX, cx, cy, scale);
      const c = project([-1, 1, v], rotY, rotX, cx, cy, scale);
      const d = project([1, 1, v], rotY, rotX, cx, cy, scale);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(c[0], c[1]); ctx.lineTo(d[0], d[1]); ctx.stroke();
    }

    // 扫描平面（上下往复光带）
    const scanY = Math.sin(t * 0.55) * 0.92;
    const corners = [
      project([-1, scanY, -1], rotY, rotX, cx, cy, scale),
      project([1, scanY, -1], rotY, rotX, cx, cy, scale),
      project([1, scanY, 1], rotY, rotX, cx, cy, scale),
      project([-1, scanY, 1], rotY, rotX, cx, cy, scale),
    ];
    ctx.beginPath();
    corners.forEach((corner, i) => (i === 0 ? ctx.moveTo(corner[0], corner[1]) : ctx.lineTo(corner[0], corner[1])));
    ctx.closePath();
    ctx.fillStyle = hexToRgba(palette.accent2, 0.1);
    ctx.fill();
    ctx.strokeStyle = hexToRgba(palette.accent2, 0.62);
    ctx.stroke();

    // 房间线框
    ctx.save();
    ctx.shadowColor = hexToRgba(palette.accent, 0.85);
    ctx.shadowBlur = 16;
    ctx.strokeStyle = hexToRgba(palette.accent2, 0.85);
    ctx.lineWidth = 1.4;
    edges.forEach(([a, b]) => {
      const pa = project([a[0] * roomSize, a[1] * roomSize * 0.72, a[2] * roomSize], rotY, rotX, cx, cy, scale);
      const pb = project([b[0] * roomSize, b[1] * roomSize * 0.72, b[2] * roomSize], rotY, rotX, cx, cy, scale);
      ctx.beginPath(); ctx.moveTo(pa[0], pa[1]); ctx.lineTo(pb[0], pb[1]); ctx.stroke();
    });
    ctx.restore();

    // 顶点光点
    cube.forEach((corner) => {
      const p = project([corner[0] * roomSize, corner[1] * roomSize * 0.72, corner[2] * roomSize], rotY, rotX, cx, cy, scale);
      ctx.fillStyle = hexToRgba(palette.accent3, 0.95);
      ctx.beginPath();
      ctx.arc(p[0], p[1], 2.2, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  const STATIC_T = 2.6;
  function frame(now) {
    draw((now - t0) / 1000);
    if (!reduced) raf = requestAnimationFrame(frame);
  }
  if (reduced) draw(STATIC_T);
  else frame(performance.now());

  const onResize = () => {
    resize();
    readPalette();
    if (reduced) draw(STATIC_T);
  };
  window.addEventListener('resize', onResize);
  return () => {
    cancelAnimationFrame(raf);
    window.removeEventListener('resize', onResize);
  };
}

export function loginView(ctx) {
  const session = getSession();
  if (session) {
    location.hash = '#/projects';
    return;
  }
  ctx.host.innerHTML = `
    <div class="aurora aurora-a"></div>
    <div class="aurora aurora-b"></div>
    <div class="login-page">
      <div class="login-visual">
        <canvas id="login-canvas"></canvas>
        <div class="lv-caption rise" style="--i:2">
          <div class="lv-eyebrow">SPATIAL INTELLIGENCE · 空间智能</div>
          <h2>手机视频进，<br>可执行的适老化改造方案出。</h2>
          <p>稠密三维重建 · 房间结构识别 · 通行净宽分析 · 正式风险评估 · 沙盒改造模拟</p>
          <div class="lv-chips">
            <span class="lv-chip">${icon('box', 14)} SLAM3R 稠密重建</span>
            <span class="lv-chip">${icon('cpu', 14)} SpatialLM 结构识别</span>
            <span class="lv-chip">${icon('shield', 14)} 正式风险评分</span>
            <span class="lv-chip">${icon('wand', 14)} AI 改造模拟</span>
          </div>
        </div>
      </div>
      <div class="login-panel">
        <div class="login-card glass rise" style="--i:1">
          <div class="lc-logo">
            <div class="brand-mark">${icon('scanframe', 22)}</div>
            <div>
              <h1>登录大屏工作台</h1>
              <div class="lc-sub">使用机构账号登录 · 数据按机构隔离</div>
            </div>
          </div>
          <form class="login-form" id="login-form" novalidate>
            <div class="field">
              <label for="login-email">邮箱</label>
              <div class="input-wrap">
                ${icon('user', 16)}
                <input class="input" id="login-email" type="email" autocomplete="username" placeholder="you@example.com" required>
              </div>
            </div>
            <div class="field">
              <label for="login-password">密码</label>
              <div class="input-wrap">
                ${icon('shield', 16)}
                <input class="input" id="login-password" type="password" autocomplete="current-password" placeholder="••••••••" required>
              </div>
            </div>
            <div class="field error hidden" id="login-error" role="alert"></div>
            <button class="btn btn-primary btn-lg" id="btn-login" type="submit">${icon('arrowRight', 17)} 登录工作台</button>
          </form>
          <div class="login-divider">或</div>
          <button class="btn btn-lg" id="btn-demo" style="width:100%">${icon('sparkles', 17)} 一键进入演示</button>
          <div class="login-foot">安龄智境 · 居家适老化安全评估系统<br>大屏工作台 · 桌面端专用（≥1280px）</div>
        </div>
      </div>
    </div>`;

  let dispose = () => {};
  try {
    dispose = startHeroCanvas(document.getElementById('login-canvas'));
  } catch (error) {
    const cap = document.querySelector('.lv-caption');
    if (cap) cap.textContent = `CANVAS ERROR: ${error?.message || error}`;
  }
  ctx.cleanup(dispose);

  const form = document.getElementById('login-form');
  const btnLogin = document.getElementById('btn-login');
  const btnDemo = document.getElementById('btn-demo');
  const errorEl = document.getElementById('login-error');

  const setLoading = (btn, loading, label) => {
    btn.classList.toggle('is-loading', loading);
    btn.innerHTML = loading ? `<span class="spinner"></span> ${label}` : btn.dataset.html;
  };
  btnLogin.dataset.html = btnLogin.innerHTML;
  btnDemo.dataset.html = btnDemo.innerHTML;

  const enter = (auth, message) => {
    setSession({ token: auth.token, user: auth.user });
    toast('ok', message, `欢迎，${auth.user?.name || '用户'}`);
    const last = localStorage.getItem('anjing.console.lastScan');
    location.hash = last ? `#/scans/${last}` : '#/projects';
  };

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    errorEl.classList.add('hidden');
    const email = document.getElementById('login-email').value.trim();
    const password = document.getElementById('login-password').value;
    if (!email || !password) {
      errorEl.textContent = '请输入邮箱和密码';
      errorEl.classList.remove('hidden');
      return;
    }
    setLoading(btnLogin, true, '正在登录…');
    try {
      const auth = await api.login(email, password);
      enter(auth, '登录成功');
    } catch (error) {
      errorEl.textContent = esc(error?.message || '登录失败');
      errorEl.classList.remove('hidden');
      setLoading(btnLogin, false);
    }
  });

  btnDemo.addEventListener('click', async () => {
    setLoading(btnDemo, true, '正在进入演示…');
    try {
      const auth = await api.demoLogin();
      enter(auth, '已进入演示环境');
    } catch (error) {
      toast('error', '演示登录失败', error?.message || '');
      setLoading(btnDemo, false);
    }
  });
}
