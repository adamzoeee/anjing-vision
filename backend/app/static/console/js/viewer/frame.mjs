/* 交付包坐标系校正（大屏查看器专用）
 *
 * 一些扫描交付包（如 scan46 的 window_wall_joined_20261008）导出自来源相机坐标系：
 * 竖直轴是 Y（up = -Y），而业务数据（structure.json / measurements.json / 2D 平面图）
 * 使用 z-up 世界系。包内自带的 views.*.R 是权威依据：OpenCV 约定相机 y 轴指向图像下方，
 * 因此来源系里的 world-up = -R[1]。
 *
 * 纯函数、无 THREE 依赖，浏览器与 node 单测共用。
 */

/* 水平视角（front/oblique/left）的相机 up 才是干净的竖直轴；俯视角
 * （overhead/overview）的 up 带俯仰分量，混进平均值会把 |up| 稀释到 0.9 边缘，
 * 一旦视角集合变化就会静默退回“不做校正”。所以只在没有水平视角时用俯视角兜底。 */
const UPRIGHT_VIEWS = ['front', 'oblique', 'left'];
const TOPDOWN_VIEWS = ['overhead', 'overview'];

function viewRows(views, keys) {
  return keys
    .map((key) => views[key])
    .filter((view) => view && Array.isArray(view.R) && Array.isArray(view.R[1]));
}

export function packUpAxis(views) {
  if (!views || typeof views !== 'object') return null;
  let rows = viewRows(views, UPRIGHT_VIEWS);
  if (!rows.length) rows = viewRows(views, UPRIGHT_VIEWS.concat(TOPDOWN_VIEWS));
  if (!rows.length) return null;
  const sum = [0, 0, 0];
  rows.forEach((view) => {
    const row = view.R[1];
    let up = [-row[0], -row[1], -row[2]];
    // 各视角的 up 可能相差 180°，先对齐到同一个半球再平均
    if (up[0] * sum[0] + up[1] * sum[1] + up[2] * sum[2] < 0) {
      up = [-up[0], -up[1], -up[2]];
    }
    sum[0] += up[0];
    sum[1] += up[1];
    sum[2] += up[2];
  });
  const length = Math.hypot(sum[0], sum[1], sum[2]);
  if (!length) return null;
  return [sum[0] / length, sum[1] / length, sum[2] / length];
}

/* 交付包的水平面还相对业务系绕竖轴转了 180°，并整体偏小（scan46 约 1.10 倍）。
 * 用“点云水平范围 ↔ 房间轮廓”拟合这一段刚体变换。
 *
 * 包内两条水平轴各自推出的尺度必须一致，才说明这段范围就是房间轮廓：
 *  - bounds：真实极值。含少量穿窗/门口走廊的点时会略微外扩，但当房间轮廓本身就是
 *    按点云极值定义的（scan46）时它是自洽的；
 *  - band  ：0.1%/99.9% 分位包络，抗离群点，但会把真实极值（贴墙点）切掉。
 * 两组输入各算一遍，选“两轴比例最一致”的那组；两组都不一致就返回 null，
 * 退回只做竖直轴映射，绝不盲目拉伸点云。 */
export function fitHorizontalFrame(pointExtent, roomBounds) {
  if (!pointExtent || !roomBounds) return null;
  const lo = roomBounds.min;
  const hi = roomBounds.max;
  if (!Array.isArray(lo) || !Array.isArray(hi)) return null;
  const roomX = Number(hi[0]) - Number(lo[0]);
  const roomY = Number(hi[1]) - Number(lo[1]);
  if (!(roomX > 1e-6) || !(roomY > 1e-6)) return null;
  const sources = [
    { name: 'bounds', x: pointExtent.x, z: pointExtent.z },
    { name: 'band', x: pointExtent.xBand, z: pointExtent.zBand },
  ];
  let best = null;
  sources.forEach((source) => {
    if (!Array.isArray(source.x) || !Array.isArray(source.z)) return;
    const spanX = Number(source.x[1]) - Number(source.x[0]);
    const spanZ = Number(source.z[1]) - Number(source.z[0]);
    if (!(spanX > 1e-6) || !(spanZ > 1e-6)) return;
    const scaleX = roomX / spanX;
    const scaleZ = roomY / spanZ;
    if (!Number.isFinite(scaleX) || !Number.isFinite(scaleZ)) return;
    const scale = (scaleX + scaleZ) / 2;
    if (scale < 0.75 || scale > 1.35) return;               // 尺度离 1 太远：视为不可信
    const spread = Math.abs(scaleX - scaleZ) / scale;
    if (!best || spread < best.spread) {
      best = { scale, spread, source: source.name, xMax: Number(source.x[1]), zMax: Number(source.z[1]) };
    }
  });
  if (!best || best.spread > 0.06) return null;               // 两轴不一致：范围不是房间轮廓
  // 水平面绕竖轴 180°：包内 +x → 业务 -x，包内 +z → 业务 -y（包内最大角对准房间最小角）
  return {
    scale: best.scale,
    offsetX: Number(lo[0]) + best.scale * best.xMax,
    offsetY: Number(lo[1]) + best.scale * best.zMax,
    flip: true,
    source: best.source,
    spread: best.spread,
  };
}

/* 返回把交付包映射进 z-up 业务系所需的校正；已经是 z-up 或无法识别时返回 null。
 * options.pointExtent / options.roomBounds 同时给出时才顺带做水平面拟合。 */
export function resolveFrame(views, floorY, options = {}) {
  const up = packUpAxis(views);
  if (!up) return null;
  const abs = up.map((value) => Math.abs(value));
  if (abs[2] > 0.9) return null;          // 交付包本身就是 z-up
  if (abs[1] <= 0.9) return null;         // 竖直轴不是 Y：不做猜测
  const floor = Number.isFinite(floorY) ? floorY : 0;
  const base = up[1] < 0
    ? { kind: 'y-vertical', sign: -1, floor }   // up = -Y：地板在最大 y 一侧
    : { kind: 'y-vertical', sign: 1, floor };   // up = +Y：地板在最小 y 一侧
  const horizontal = fitHorizontalFrame(options?.pointExtent, options?.roomBounds);
  return horizontal ? { ...base, horizontal } : base;
}

/* 单点校正：把来源帧坐标映射到 z-up 业务系。
 * 竖直轴翻转、水平面 180° 偏转与整体尺度都在这一个函数里，
 * 点云与展示层必须走同一份实现，否则两者会再次错开（scan46 的回归点）。 */
export function framePointInto(target, index, x, y, z, frame) {
  if (!frame) {
    target[index] = x;
    target[index + 1] = y;
    target[index + 2] = z;
    return target;
  }
  const fitted = frame.horizontal || null;
  const scale = fitted ? fitted.scale : 1;
  const mapX = fitted ? fitted.offsetX - scale * x : x;
  const mapY = fitted ? fitted.offsetY - scale * z : z;
  if (frame.sign < 0) {
    target[index] = mapX;
    target[index + 1] = mapY;
    target[index + 2] = scale * (frame.floor - y);
  } else {
    target[index] = mapX;
    target[index + 1] = -mapY;
    target[index + 2] = scale * (y - frame.floor);
  }
  return target;
}

export function framePoint(x, y, z, frame) {
  return framePointInto([0, 0, 0], 0, x, y, z, frame);
}

export function percentile(values, pct) {
  if (!values || !values.length) return null;
  const sorted = Array.from(values).sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.round((pct / 100) * (sorted.length - 1))));
  return sorted[index];
}

/* 点云包络：点云干净（极值只比 0.1%/99.9% 分位多出不到 1% 跨度）时用真实极值，
 * 否则说明有离群点，改用分位值，避免个别点把整包点云拉伸。 */
export function envelope(values) {
  if (!values || !values.length) return null;
  let min = Infinity;
  let max = -Infinity;
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    if (value < min) min = value;
    if (value > max) max = value;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  const low = percentile(values, 0.1);
  const high = percentile(values, 99.9);
  const span = Math.max(high - low, 1e-6);
  return [
    low - min <= span * 0.01 ? min : low,
    max - high <= span * 0.01 ? max : high,
  ];
}

/* 竖直方向的“地板面”估计：地板是点云里最密的水平面之一，取竖直范围靠下
 * （up=-Y 时是最大 y 一侧）那一段里点最密的一层。极端分位数会落在平面之外的
 * 离群点上，把整包点云抬高十几厘米，所以不要用它当竖直锚点。 */
export function modalPlane(values, upSign, sideFraction = 0.35, bins = 320) {
  if (!values || !values.length) return null;
  let min = Infinity;
  let max = -Infinity;
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    if (value < min) min = value;
    if (value > max) max = value;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max) || max - min < 1e-6) return null;
  const width = (max - min) / bins;
  const hist = new Uint32Array(bins);
  for (let i = 0; i < values.length; i += 1) {
    const slot = Math.min(bins - 1, Math.max(0, Math.floor((values[i] - min) / width)));
    hist[slot] += 1;
  }
  const floorSide = upSign < 0;
  const from = floorSide ? Math.floor(bins * (1 - sideFraction)) : 0;
  const to = floorSide ? bins : Math.ceil(bins * sideFraction);
  let best = -1;
  for (let slot = from; slot < to; slot += 1) {
    if (best < 0 || hist[slot] > hist[best]) best = slot;
  }
  if (best < 0) return null;
  return min + (best + 0.5) * width;
}
