/* 结构数据归一化（大屏查看器 / 房间工作台 / 大屏模式共用）
 *
 * 交付包（scan45/46）的 structure_calibrated.json 同时保留两套家具字段：
 *  - objects           ：旧 SpatialLM 候选框，scan46 只有 5 件，位置是候选坐标，会互相重叠；
 *  - semantic_instances：审计后的实例（scan46 为 6 件），与 measurements.json、2D 平面图、
 *                        通行分析与评分逐项同一实例。
 * 有 semantic_instances 时必须优先它；旧 objects 只作为回退。
 * 位置、尺寸、旋转一律取自同一条实例记录，不与测量尺寸混用。
 *
 * 纯函数、无 THREE / DOM 依赖，浏览器与 node 单测共用。
 */

const SUFFIX = /_\d+$/;

function categoryOf(item, kind) {
  const named = item.category || item.label || item.type;
  if (named) return String(named);
  const stem = String(item.instance_id || item.id || '').replace(SUFFIX, '');
  return stem || kind;
}

export function structureFurniture(source) {
  const semantic = Array.isArray(source?.semantic_instances) ? source.semantic_instances : [];
  if (semantic.length) return semantic;
  return Array.isArray(source?.objects) ? source.objects : [];
}

export function normalizeStructure(source) {
  const data = source || {};
  const pass = (items, kind) => (items || [])
    .filter((item) => item && Array.isArray(item.center) && Array.isArray(item.size))
    .map((item) => ({
      center: item.center,
      size: item.size,
      rotation_z_deg: item.rotation_z_deg || 0,
      category: categoryOf(item, kind),
      instance_id: item.instance_id || item.id || `${kind}_${Math.random().toString(36).slice(2, 7)}`,
    }));
  return {
    walls: pass(data.walls, 'wall'),
    doors: pass(data.doors, 'door'),
    windows: pass(data.windows, 'window'),
    objects: pass(structureFurniture(data), 'object'),
  };
}
