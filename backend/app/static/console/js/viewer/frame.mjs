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

/* 返回把交付包映射进 z-up 业务系所需的校正；已经是 z-up 或无法识别时返回 null。 */
export function resolveFrame(views, floorY) {
  const up = packUpAxis(views);
  if (!up) return null;
  const abs = up.map((value) => Math.abs(value));
  if (abs[2] > 0.9) return null;          // 交付包本身就是 z-up
  if (abs[1] <= 0.9) return null;         // 竖直轴不是 Y：不做猜测
  const floor = Number.isFinite(floorY) ? floorY : 0;
  return up[1] < 0
    ? { kind: 'y-vertical', sign: -1, floor }   // up = -Y：地板在最大 y 一侧
    : { kind: 'y-vertical', sign: 1, floor };   // up = +Y：地板在最小 y 一侧
}

/* 单点校正：把来源帧坐标映射到 z-up 业务系。 */
export function framePoint(x, y, z, frame) {
  if (!frame) return [x, y, z];
  return frame.sign < 0
    ? [x, z, frame.floor - y]
    : [x, -z, y - frame.floor];
}

export function percentile(values, pct) {
  if (!values || !values.length) return null;
  const sorted = Array.from(values).sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.round((pct / 100) * (sorted.length - 1))));
  return sorted[index];
}
