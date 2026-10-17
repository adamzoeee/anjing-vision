/* 登录页：品牌 3D 粒子视觉（温馨户型的框架图）+ 玻璃登录卡 */

import { api, getSession, setSession } from '../api.js?v=house-scan-1';
import { icon } from '../icons.js';
import { toast, esc } from '../ui.js';
import { HOME_MODEL } from '../home-plan.mjs';

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

  /* 户型框架图：墙体 / 门 / 窗 / 家具一次性投影，按类别分层描边 */
  const homeSegments = {
    walls: HOME_MODEL.segments.filter((seg) => seg.kind === 'walls'),
    openings: HOME_MODEL.segments.filter((seg) => seg.kind === 'doors' || seg.kind === 'windows'),
    objects: HOME_MODEL.segments.filter((seg) => seg.kind === 'objects'),
  };
  const footprint = HOME_MODEL.footprint;

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

    // 地板：沿户型外轮廓铺一块，并画室内网格
    const floor = footprint.map((p) => project(p, rotY, rotX, cx, cy, scale));
    ctx.beginPath();
    floor.forEach((p, i) => (i === 0 ? ctx.moveTo(p[0], p[1]) : ctx.lineTo(p[0], p[1])));
    ctx.closePath();
    ctx.fillStyle = hexToRgba(palette.accent3, 0.07);
    ctx.fill();
    ctx.strokeStyle = hexToRgba(palette.accent2, 0.34);
    ctx.lineWidth = 1.1;
    ctx.stroke();

    const { minX, maxX, minZ, maxZ } = HOME_MODEL.bounds;
    ctx.strokeStyle = hexToRgba(palette.accent, 0.14);
    ctx.lineWidth = 1;
    const gridN = 8;
    for (let i = 1; i < gridN; i++) {
      const gx = minX + ((maxX - minX) * i) / gridN;
      const gz = minZ + ((maxZ - minZ) * i) / gridN;
      const a = project([gx, 0, minZ], rotY, rotX, cx, cy, scale);
      const b = project([gx, 0, maxZ], rotY, rotX, cx, cy, scale);
      const c = project([minX, 0, gz], rotY, rotX, cx, cy, scale);
      const d = project([maxX, 0, gz], rotY, rotX, cx, cy, scale);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(c[0], c[1]); ctx.lineTo(d[0], d[1]); ctx.stroke();
    }

    // 扫描平面：在户型里上下往复，示意「正在重建的家」
    const planeY = (Math.sin(t * 0.5) * 0.5 + 0.5) * HOME_MODEL.height;
    const slab = footprint.map(([fx, , fz]) => project([fx, planeY, fz], rotY, rotX, cx, cy, scale));
    ctx.beginPath();
    slab.forEach((p, i) => (i === 0 ? ctx.moveTo(p[0], p[1]) : ctx.lineTo(p[0], p[1])));
    ctx.closePath();
    ctx.fillStyle = hexToRgba(palette.accent2, 0.09);
    ctx.fill();
    ctx.strokeStyle = hexToRgba(palette.accent2, 0.5);
    ctx.lineWidth = 1;
    ctx.stroke();

    // 户型框架图：家具最轻、门窗次之、墙体最重
    const strokeSegments = (segments, color, alpha, width) => {
      if (!segments.length) return;
      ctx.strokeStyle = hexToRgba(color, alpha);
      ctx.lineWidth = width;
      ctx.beginPath();
      segments.forEach(({ a, b }) => {
        const pa = project(a, rotY, rotX, cx, cy, scale);
        const pb = project(b, rotY, rotX, cx, cy, scale);
        ctx.moveTo(pa[0], pa[1]);
        ctx.lineTo(pb[0], pb[1]);
      });
      ctx.stroke();
    };
    ctx.save();
    ctx.shadowColor = hexToRgba(palette.accent, 0.7);
    ctx.shadowBlur = 12;
    strokeSegments(homeSegments.walls, palette.accent2, 0.9, 1.35);
    ctx.restore();
    strokeSegments(homeSegments.openings, palette.accent2, 0.42, 0.9);
    strokeSegments(homeSegments.objects, palette.accent3, 0.5, 0.85);
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
  const registering = location.hash.startsWith('#/register');
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
              <h1>${registering ? '创建机构账号' : '登录大屏工作台'}</h1>
              <div class="lc-sub">${registering ? '创建新机构并成为管理员' : '使用机构账号登录 · 数据按机构隔离'}</div>
            </div>
          </div>
          <form class="login-form" id="login-form" novalidate>
            ${registering ? `
            <div class="field">
              <label for="register-org">机构名称</label>
              <div class="input-wrap">${icon('building', 16)}
                <input class="input" id="register-org" type="text" autocomplete="organization" maxlength="120" placeholder="例如：幸福养老院" required>
              </div>
            </div>
            <div class="field">
              <label for="register-name">您的姓名</label>
              <div class="input-wrap">${icon('user', 16)}
                <input class="input" id="register-name" type="text" autocomplete="name" maxlength="80" placeholder="例如：张主任" required>
              </div>
            </div>` : ''}
            <div class="field">
              <div class="field-label-row">
                <label for="login-email">邮箱</label>
                <span class="demo-credential" id="login-email-demo">演示：demo@anjing-vision.com</span>
              </div>
              <div class="input-wrap">
                ${icon('user', 16)}
                <input class="input" id="login-email" type="email" autocomplete="username" maxlength="120" placeholder="you@example.com" aria-describedby="login-email-demo" required>
              </div>
            </div>
            <div class="field">
              <div class="field-label-row">
                <label for="login-password">密码</label>
                <span class="demo-credential" id="login-password-demo">演示：demo@anjing-vision.com</span>
              </div>
              <div class="input-wrap">
                ${icon('shield', 16)}
                <input class="input" id="login-password" type="password" autocomplete="${registering ? 'new-password' : 'current-password'}" maxlength="64" minlength="${registering ? '8' : '1'}" placeholder="${registering ? '至少 8 位' : '••••••••'}" aria-describedby="login-password-demo" required>
              </div>
            </div>
            ${registering ? `
            <div class="field">
              <label for="register-confirm">确认密码</label>
              <div class="input-wrap">${icon('shield', 16)}
                <input class="input" id="register-confirm" type="password" autocomplete="new-password" maxlength="64" placeholder="再次输入密码" required>
              </div>
            </div>` : ''}
            <div class="field error hidden" id="login-error" role="alert"></div>
            <button class="btn btn-primary btn-lg" id="btn-login" type="submit">${icon('arrowRight', 17)} ${registering ? '创建并进入工作台' : '登录工作台'}</button>
          </form>
          <div class="login-switch">
            ${registering ? '已有机构账号？ <a href="#/login">返回登录</a>' : '还没有机构账号？ <a href="#/register">创建新机构</a>'}
          </div>
          ${registering ? '<p class="login-note">已有机构的成员账号由机构管理员创建。</p>' : ''}
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
  const errorEl = document.getElementById('login-error');

  const setLoading = (btn, loading, label) => {
    btn.classList.toggle('is-loading', loading);
    btn.disabled = loading;
    btn.innerHTML = loading ? `<span class="spinner"></span> ${label}` : btn.dataset.html;
  };
  btnLogin.dataset.html = btnLogin.innerHTML;

  const enter = (auth, message) => {
    setSession({ token: auth.token, user: auth.user });
    toast('ok', message, `欢迎，${auth.user?.name || '用户'}`);
    /* 登录后统一回到项目工作台；房间/整屋入口由侧边栏与项目页进入 */
    location.hash = '#/projects';
  };

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    errorEl.classList.add('hidden');
    const orgName = registering ? document.getElementById('register-org').value.trim() : '';
    const name = registering ? document.getElementById('register-name').value.trim() : '';
    const email = document.getElementById('login-email').value.trim();
    const password = document.getElementById('login-password').value;
    const confirm = registering ? document.getElementById('register-confirm').value : '';
    if (registering && (!orgName || !name)) {
      errorEl.textContent = '请输入机构名称和姓名';
      errorEl.classList.remove('hidden');
      return;
    }
    if (!email || !password || !/^\S+@\S+\.\S+$/.test(email)) {
      errorEl.textContent = '请输入有效邮箱和密码';
      errorEl.classList.remove('hidden');
      return;
    }
    if (registering && (password.length < 8 || password !== confirm)) {
      errorEl.textContent = password.length < 8 ? '密码至少 8 位' : '两次输入的密码不一致';
      errorEl.classList.remove('hidden');
      return;
    }
    setLoading(btnLogin, true, registering ? '正在创建…' : '正在登录…');
    try {
      const auth = registering
        ? await api.register(orgName, name, email, password)
        : await api.login(email, password);
      enter(auth, registering ? '机构创建成功' : '登录成功');
    } catch (error) {
      errorEl.textContent = error?.message || (registering ? '创建失败' : '登录失败');
      errorEl.classList.remove('hidden');
      setLoading(btnLogin, false);
    }
  });

}
