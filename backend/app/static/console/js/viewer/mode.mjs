/* 视图模式的图层可见性（console 点云查看器 / node 单测共用）
 *
 * 用户口径（2026-10-09）：“真实场景”只渲染点云与窗边展示层，结构框架（墙/门/窗/
 * 家具/标签）只在“空间结构”里出现；结构模式下点云隐藏、框架按图层开关显示，
 * 背景网格启用。未知模式一律按真实场景处理（宁可少画，不要多画）。 */
export function layerVisibility(mode, layers = {}, hasLayout = false) {
  const structure = mode === 'structure';
  const on = (key) => layers[key] !== false;
  return {
    pcd: structure ? false : on('pcd'),
    boxes: structure && hasLayout,
    labels: structure && hasLayout && on('labels'),
    grid: structure,
    walls: on('walls'),
    doors: on('doors'),
    windows: on('windows'),
    objects: on('objects'),
  };
}
