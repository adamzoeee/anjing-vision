/* 交付包坐标系校正（大屏查看器专用）
 *
 * 一些扫描交付包（如 scan46 的 window_wall_joined_20261008）导出自来源相机坐标系：
 * 竖直轴是 Y（up = -Y），而业务数据（structure.json / measurements.json / 2D 平面图）
 * 使用 z-up 世界系。包内自带的 views.*.R 是权威依据：OpenCV 约定相机 y 轴指向图像下方，
 * 因此来源系里的 world-up = -R[1]。
 *
 * 纯函数、无 THREE 依赖，浏览器与 node 单测共用。
 */

export function packUpAxis(views) {
  if (!views || typeof views !== 'object') return null;
  const rows = ['front', 'oblique', 'overhead', 'overview', 'left']
    .map((key) => views[key])
    .filter((view) => view && Array.isArray(view.R) && Array.isArray(view.R[1]));
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
 * 用“点云水平包络 ↔ 房间轮廓”拟合这一段刚体变换：
 * 两轴各自推算的尺度必须一致，否则说明包络不是房间轮廓（被裁剪或含离群点），
 * 此时返回 null，退回只做竖直轴映射，绝不盲目拉伸点云。 */
export function fitHorizontalFrame(pointExtent, roomBounds) {
  if (!pointExtent || !roomBounds) return null;
  const xs = pointExtent.x;
  const zs = pointExtent.z;
  const lo = roomBounds.min;
  const hi = roomBounds.max;
  if (!Array.isArray(xs) || !Array.isArray(zs) || !Array.isArray(lo) || !Array.isArray(hi)) return null;
  const spanX = Number(xs[1]) - Number(xs[0]);
  const spanZ = Number(zs[1]) - Number(zs[0]);
  const roomX = Number(hi[0]) - Number(lo[0]);
  const roomY = Number(hi[1]) - Number(lo[1]);
  if (!(spanX > 1e-6) || !(spanZ > 1e-6) || !(roomX > 1e-6) || !(roomY > 1e-6)) return null;
  const scaleX = roomX / spanX;
  const scaleZ = roomY / spanZ;
  if (!Number.isFinite(scaleX) || !Number.isFinite(scaleZ)) return null;
  const scale = (scaleX + scaleZ) / 2;
  if (scale < 0.75 || scale > 1.35) return null;              // 尺度离 1 太远：视为不可信
  if (Math.abs(scaleX - scaleZ) / scale > 0.06) return null;  // 两轴不一致：包络不是房间轮廓
  // 水平面绕竖轴 180°：包内 +x → 业务 -x，包内 +z → 业务 -y（包内 z 最大角对准房间最小角）
  return { scale, offsetX: Number(lo[0]) + scale * Number(xs[1]), offsetY: Number(lo[1]) + scale * Number(zs[1]), flip: true };
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

/* 单点校正：把来源帧坐标映射到 z-up 业务系。 */
export function framePoint(x, y, z, frame) {
  if (!frame) return [x, y, z];
  const fitted = frame.horizontal || null;
  const scale = fitted ? fitted.scale : 1;
  const mapX = fitted ? fitted.offsetX - scale * x : x;
  const mapY = fitted ? fitted.offsetY - scale * z : z;
  return frame.sign < 0
    ? [mapX, mapY, scale * (frame.floor - y)]
    : [mapX, -mapY, scale * (y - frame.floor)];
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
