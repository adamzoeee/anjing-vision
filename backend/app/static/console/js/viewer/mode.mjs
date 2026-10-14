/* 视图模式的图层可见性（console 点云查看器 / node 单测共用）
 *
 * “真实场景”只渲染点云；“空间结构”只渲染框架；整屋专用“叠加”同时显示
 * 点云和框架。未知模式一律按真实场景处理。 */
export function layerVisibility(mode, layers = {}, hasLayout = false) {
  const structure = mode === 'structure';
  const overlay = mode === 'overlay';
  const framework = structure || overlay;
  const on = (key) => layers[key] !== false;
  return {
    pcd: structure ? false : on('pcd'),
    boxes: framework && hasLayout,
    labels: framework && hasLayout && on('labels'),
    grid: structure,
    walls: on('walls'),
    doors: on('doors'),
    windows: on('windows'),
    objects: on('objects'),
  };
}
