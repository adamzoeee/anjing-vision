# Design System Master File

> **LOGIC:** When building a specific page, first check `design-system/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the rules below.

---

**Project:** Anjing Vision
**Generated:** 2026-10-08 21:46:45
**Category:** Spatial Computing OS / App
**Design Dials:** Variance 7/10 (Balanced / Modern) | Motion 8/10 (Complex) | Density 6/10 (Standard)

---

## Global Rules

### Color Palette

| Role | Hex | CSS Variable |
|------|-----|--------------|
| Primary | `#FFFFFF` | `--color-primary` |
| On Primary | `#0F172A` | `--color-on-primary` |
| Secondary | `#E5E5E5` | `--color-secondary` |
| On Secondary | `#0F172A` | `--color-on-secondary` |
| Accent/CTA | `#FFFFFF` | `--color-accent` |
| On Accent/CTA | `#0F172A` | `--color-on-accent` |
| Background | `#888888` | `--color-background` |
| Foreground | `#000000` | `--color-foreground` |
| Card | `#999999` | `--color-card` |
| Card Foreground | `#000000` | `--color-card-foreground` |
| Muted | `#E5E7EB` | `--color-muted` |
| Muted Foreground | `#5F6673` | `--color-muted-foreground` |
| Border | `#CCCCCC` | `--color-border` |
| Destructive | `#FF3B30` | `--color-destructive` |
| On Destructive | `#000000` | `--color-on-destructive` |
| Ring | `#000000` | `--color-ring` |

**Color Notes:** Glass white + system blue [Accent adjusted from #007AFF]

### Typography

- **Heading Font:** Inter
- **Body Font:** Inter
- **Mood:** spatial, legible, glass, system, clean, neutral
- **Google Fonts:** [Inter + Inter](https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap)

**CSS Import:**
```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');
```

### Spacing Variables

*Density: 6/10 — Standard*

| Token | Value | Usage |
|-------|-------|-------|
| `--space-xs` | `4px` / `0.25rem` | Tight gaps |
| `--space-sm` | `8px` / `0.5rem` | Icon gaps, inline spacing |
| `--space-md` | `16px` / `1rem` | Standard padding |
| `--space-lg` | `24px` / `1.5rem` | Section padding |
| `--space-xl` | `32px` / `2rem` | Large gaps |
| `--space-2xl` | `48px` / `3rem` | Section margins |
| `--space-3xl` | `64px` / `4rem` | Hero padding |

### Shadow Depths

| Level | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.05)` | Subtle lift |
| `--shadow-md` | `0 4px 6px rgba(0,0,0,0.1)` | Cards, buttons |
| `--shadow-lg` | `0 10px 15px rgba(0,0,0,0.1)` | Modals, dropdowns |
| `--shadow-xl` | `0 20px 25px rgba(0,0,0,0.15)` | Hero images, featured cards |

---

## Component Specs

### Buttons

```css
/* Primary Button */
.btn-primary {
  background: #FFFFFF;
  color: #0F172A;
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  transition: all 200ms ease;
  cursor: pointer;
}

.btn-primary:hover {
  opacity: 0.9;
  transform: translateY(-1px);
}

/* Secondary Button */
.btn-secondary {
  background: transparent;
  color: #000000;
  border: 2px solid #FFFFFF;
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  transition: all 200ms ease;
  cursor: pointer;
}
```

### Cards

```css
.card {
  background: #888888;
  border-radius: 12px;
  padding: 24px;
  box-shadow: var(--shadow-md);
  transition: all 200ms ease;
  cursor: pointer;
}

.card:hover {
  box-shadow: var(--shadow-lg);
  transform: translateY(-2px);
}
```

### Inputs

```css
.input {
  padding: 12px 16px;
  border: 1px solid #E2E8F0;
  border-radius: 8px;
  font-size: 16px;
  transition: border-color 200ms ease;
}

.input:focus {
  border-color: #FFFFFF;
  outline: none;
  box-shadow: 0 0 0 3px #FFFFFF20;
}
```

### Modals

```css
.modal-overlay {
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
}

.modal {
  background: white;
  border-radius: 16px;
  padding: 32px;
  box-shadow: var(--shadow-xl);
  max-width: 500px;
  width: 90%;
}
```

---

## Style Guidelines

**Style:** Spatial UI (VisionOS)

**Keywords:** Glass, depth, immersion, spatial, translucent, gaze, gesture, apple, vision-pro

**Best For:** Spatial computing apps, VR/AR interfaces, immersive media, futuristic dashboards

**Key Effects:** Parallax depth, dynamic lighting response, gaze-hover effects, smooth scale on focus

### Page Pattern

**Pattern Name:** Immersive/Interactive Experience

- **Conversion Strategy:** Measure engagement for the specific audience and device mix. Performance trade-off. Provide skip option. Mobile fallback essential. Provide skip, keyboard, reduced-motion, and non-3D fallback paths. Pause animation when offscreen/hidden and preserve the completed final state when reduced motion is enabled.
- **CTA Placement:** After interaction complete + Skip option for impatient users
- **Section Order:** Full-screen interactive element > Guided product tour > Key benefits revealed > CTA after completion

---

## Motion

**Hover Micro-interaction** (Complex) — Trigger: hover + mousemove | Duration: 300-500ms | Easing: `elastic.out(1,0.4)`

```js
const onPointerMove = (e) => { const r = el.getBoundingClientRect(); xTo((e.clientX - r.left - r.width / 2) * 0.3); yTo((e.clientY - r.top - r.height / 2) * 0.3); }; el.addEventListener('pointermove', onPointerMove); return () => el.removeEventListener('pointermove', onPointerMove);
```

**Framework notes:** Keep a stable named pointer handler so cleanup removes the same function; in React/Vue return the removeEventListener cleanup; use gsap.matchMedia('(prefers-reduced-motion: reduce)') and render x/y at the final neutral state

- ✅ Clamp the pull strength (e.g. * 0.3) so the element never fully leaves its hit box
- ❌ Don't apply magnetic effect to more than 1-2 focal elements per screen; it becomes noisy
- ⚡ Use will-change: transform on the target element for smoother compositing

---

## Anti-Patterns (Do NOT Use)

- ❌ 2D design
- ❌ No spatial depth

### Additional Forbidden Patterns

- ❌ **Emojis as icons** — Use SVG icons (Heroicons, Lucide, Simple Icons)
- ❌ **Missing cursor:pointer** — All clickable elements must have cursor:pointer
- ❌ **Layout-shifting hovers** — Avoid scale transforms that shift layout
- ❌ **Low contrast text** — Maintain 4.5:1 minimum contrast ratio
- ❌ **Instant state changes** — Always use transitions (150-300ms)
- ❌ **Invisible focus states** — Focus states must be visible for a11y

---

## Pre-Delivery Checklist

Before delivering any UI code, verify:

- [ ] No emojis used as icons (use SVG instead)
- [ ] All icons from consistent icon set (Heroicons/Lucide)
- [ ] `cursor-pointer` on all clickable elements
- [ ] Hover states with smooth transitions (150-300ms)
- [ ] Light mode: text contrast 4.5:1 minimum
- [ ] Focus states visible for keyboard navigation
- [ ] `prefers-reduced-motion` respected
- [ ] Responsive: 375px, 768px, 1024px, 1440px
- [ ] No content hidden behind fixed navbars
- [ ] No horizontal scroll on mobile

---

## Project Override — Dark Spatial Cockpit（2026-10-08）

> 本 override 由用户确认后追加，**优先级高于上方默认推荐**，本仓库实现以本节为准。
> 依据：产品 C 位是深色 3D 点云场景（既有引擎背景 `#10141B`），浅色玻璃会与场景冲突；
> 色板基线取自 `colors.csv`「Smart Home/IoT Dashboard · Dark tech + status green」深色族，
> 并结合产品既有语义色（墙/门/窗/家具）。**仅发布暗色模式**，产品定位为桌面大屏（≥1280px），
> 用户明确要求不做移动端适配，因此上方 375/768 响应式检查项不适用。

### 授权色板（CSS Variables）

| Role | Hex | Var | 用途 |
|------|-----|-----|------|
| Background 0 | `#05070F` | `--bg-0` | 页面最远景 |
| Background 1 | `#0A0F1E` | `--bg-1` | 页面基底 |
| Background 2 | `#0F172A` | `--bg-2` | 场景/面板底 |
| Panel | `rgba(148,180,255,.055)` | `--panel` | 玻璃面板填充 |
| Panel Strong | `rgba(148,180,255,.09)` | `--panel-strong` | 强调面板/悬浮 |
| Border | `rgba(148,180,255,.14)` | `--border` | 玻璃描边 |
| Border Strong | `rgba(148,180,255,.30)` | `--border-strong` | 悬浮/激活描边 |
| Text 1 | `#EAF0FB` | `--text-1` | 主文本 |
| Text 2 | `#A8B4CC` | `--text-2` | 次要文本 |
| Text 3 | `#6B7793` | `--text-3` | 弱化/标签 |
| Accent | `#4F9CF9` | `--accent` | 主强调/CTA |
| Accent 2 | `#7DD3FC` | `--accent-2` | 渐变终点/高光 |
| Accent 3 | `#8B7CFF` | `--accent-3` | 次强调（图表区分） |
| Wall | `#4F9CF9` | `--wall` | 墙（3D 与图例一致） |
| Door | `#FFB703` | `--door` | 门 |
| Window | `#5CE98A` | `--window` | 窗 |
| Furniture | `#FF8FA3` | `--furniture` | 家具 |
| OK | `#5CE98A` | `--ok` | 低风险/达标 |
| Warn | `#FFB703` | `--warn` | 中风险 |
| Bad | `#FF5A6E` | `--bad` | 高风险 |

### 字体（离线零 CDN，不使用 Google Fonts）

部署环境为本地裸机且要求无外网依赖，`Inter` 仅在本机已安装时生效，否则回退 Segoe UI / 微软雅黑。

```css
--font-sans: "Inter", "Segoe UI Variable Text", "Segoe UI", system-ui, "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
--font-mono: ui-monospace, "Cascadia Mono", "JetBrains Mono", Consolas, monospace;
```

数字（评分/尺寸/百分比）一律 `font-variant-numeric: tabular-nums`，走 mono 栈。

### 组件规范（暗色玻璃）

```css
.glass {
  background: linear-gradient(160deg, rgba(148,180,255,.075), rgba(148,180,255,.03));
  border: 1px solid rgba(148,180,255,.14);
  border-radius: 16px;
  backdrop-filter: blur(18px) saturate(140%);
  box-shadow: 0 24px 48px -12px rgba(2,6,18,.6), inset 0 1px 0 rgba(255,255,255,.06);
}
.btn-primary {
  background: linear-gradient(135deg, #4F9CF9, #7DD3FC);
  color: #06101F;
  box-shadow: 0 8px 24px -6px rgba(79,156,249,.55);
}
```

### 动效基线

- 入场 stagger：60ms 递增，`cubic-bezier(.16,1,.3,1)`，单段 ≤600ms
- 数值计数：800ms easeOutCubic；环形仪表达标动画 1.2s
- 背景 aurora 漂浮：24–40s 循环；扫描光束 6–8s 循环
- 全部动效受 `prefers-reduced-motion: reduce` 控制（直接呈现终态）

### 本 override 的校验清单

- [x] 仅暗色模式；对比度正文 ≥ 4.5:1（text-1/text-2 于 bg 系验证）
- [x] 图标全部内联 SVG（Lucide 风格），零 emoji
- [x] 所有可点击元素 cursor:pointer + :focus-visible 光圈
- [x] 动效可被 prefers-reduced-motion 关闭
- [x] 桌面大屏专用：≥1280px；更窄仅显示提示门帘（用户确认无移动端）

---

## v2 Override — 双主题「暮光 / 象牙」（2026-10-08 第二轮反馈后）

> 用户反馈：深蓝+青玻璃配色看腻、动画不够柔和、缺少视图切换与页面转场、界面背后缺少悬浮 3D。
> v2 为当前实现的**权威规范**，v1 蓝黑配色作废。

### 主题（可在工作台左下角一键切换，`data-skin`）

| Token | dusk 暮光（默认） | ivory 象牙 |
|-------|------------------|-----------|
| bg | `#120D1E → #1A1130`（深紫暮色） | `#F3EDE5 → #FAF6F0`（暖象牙） |
| accent | `#FF8E6B → #FFC9A3`（珊瑚蜜桃） | `#F0562D → #FFA25C` |
| accent-3 | `#A78BFA`（紫罗兰） | `#7C5CF0` |
| panel | 白色 5.5% 玻璃 | 白色 72% 玻璃 |
| well（3D 深井） | `#0D0A16` | `#17122A` |
| 语义色 | 墙紫 `#9B8CFF` / 门琥珀 `#FFC24B` / 窗薄荷 `#6EE7B7` / 家具玫瑰 `#FF9DB1` | 对应加深版 |

### 动画规范（柔和优先）

- 统一缓动 `--ease-soft: cubic-bezier(.22,1,.36,1)`，入场 900ms、状态 360ms、微交互 220ms
- 入场带纵深：`translateY(22px) scale(.988)` → 原位，stagger 90ms
- 页面转场：View Transitions API（旧页模糊上浮 / 新页模糊下沉）+ 光带扫掠 1.1s
- 3D 镜头：二次贝塞尔弧线入场 3.6s；autoRotateSpeed 0.5、damping 0.055（丝滑）
- 系统「减少动效」时降级为纯透明度过渡；工作台提供「全效动效」开关覆盖

### 关键能力

- **视图切换**：真实场景（稠密点云）/ 空间结构（半透明墙体 + 结构框 + 地面网格 + 家具标签）
- **悬浮 3D 背景**：`#bg3d` 固定层渲染真实 layout 线框房间 + 尘点，随主题换色、随鼠标视差、随路由调节透明度
