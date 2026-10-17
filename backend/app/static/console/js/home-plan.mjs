/* 温馨主卧的示意户型：登录页 hero 与工作台背景层共用的「家的框架图」。
 * 字段与 layout.json 一致（center / size / rotation_z_deg，z-up，单位米），
 * 因此可直接喂给 three.js 背景层；HOME_MODEL 是同一份数据在 2D 投影坐标系下的归一化结果。
 * 只有一个房间：床、床头柜、衣柜、单人椅、绿植，门与窗各一处——背景层保持干净。 */

const WALL_H = 1.0;      /* 剖切到 1m：像建筑模型一样能看清室内布置 */
const WALL_T = 0.12;
const DOOR_H = 1.0;
const SILL_H = 0.35;     /* 窗台矮板，用来示意窗洞位置 */

/* 沿 x 延展的墙（法线朝 y）；沿 y 延展的墙（法线朝 x） */
const hWall = (x0, x1, y) => ({
  center: [(x0 + x1) / 2, y, WALL_H / 2],
  size: [x1 - x0, WALL_T, WALL_H],
  rotation_z_deg: 0,
});
const vWall = (y0, y1, x) => ({
  center: [x, (y0 + y1) / 2, WALL_H / 2],
  size: [WALL_T, y1 - y0, WALL_H],
  rotation_z_deg: 0,
});
const box = (center, size, rotation_z_deg = 0) => ({ center, size, rotation_z_deg });
/* 家具：给平面位置、占地和高度，自动落到地面上 */
const piece = (x, y, w, d, h, rotation_z_deg = 0) => box([x, y, h / 2], [w, d, h], rotation_z_deg);

/* 主卧（米；x 向东，y 向北，z 向上）：净尺寸 4.2 × 3.6 m
 *   床头靠西墙，衣柜贴东墙，南墙开门，北墙开窗，房间中南部一把单人椅 + 西南角绿植 */
export const HOME_LAYOUT = {
  walls: [
    hWall(-2.1, -0.5, 1.8),      /* 北墙（留窗洞） */
    hWall(0.9, 2.1, 1.8),
    hWall(-2.1, 0.7, -1.8),      /* 南墙（留门洞） */
    hWall(1.6, 2.1, -1.8),
    vWall(-1.8, 1.8, -2.1),      /* 西墙 */
    vWall(-1.8, 1.8, 2.1),       /* 东墙 */
  ],
  doors: [
    box([1.15, -1.8, DOOR_H / 2], [0.9, 0.1, DOOR_H]),      /* 房门（南墙） */
  ],
  windows: [
    piece(0.2, 1.8, 1.4, 0.1, SILL_H),                     /* 窗（北墙） */
  ],
  objects: [
    piece(-1.29, 0.1, 1.5, 2.0, 0.6),                      /* 双人床（床头靠西墙） */
    piece(-1.83, 1.31, 0.42, 0.42, 0.5),                   /* 床头柜（北） */
    piece(-1.83, -1.11, 0.42, 0.42, 0.5),                  /* 床头柜（南） */
    piece(1.74, 0.6, 0.6, 1.6, 2.0),                       /* 衣柜（贴东墙） */
    piece(0.35, -0.8, 0.72, 0.7, 0.76, -18),               /* 单人椅 */
    piece(-1.8, -1.5, 0.4, 0.4, 0.7),                      /* 绿植（西南角） */
  ],
};

/* 房间外轮廓（用于铺一块地板底），顺序绕行一圈 */
export const HOME_FOOTPRINT = [[-2.1, -1.8], [2.1, -1.8], [2.1, 1.8], [-2.1, 1.8]];

const BOX_KEYS = ['walls', 'doors', 'windows', 'objects'];

/* 含旋转的轴对齐包围盒 */
export function layoutBounds(layout = HOME_LAYOUT) {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  BOX_KEYS.forEach((key) => (layout[key] || []).forEach((item) => {
    const rad = ((item.rotation_z_deg || 0) * Math.PI) / 180;
    const cos = Math.abs(Math.cos(rad));
    const sin = Math.abs(Math.sin(rad));
    const [sx, sy, sz] = item.size;
    const hx = (cos * sx + sin * sy) / 2;
    const hy = (sin * sx + cos * sy) / 2;
    const hz = sz / 2;
    [hx, hy, hz].forEach((half, axis) => {
      min[axis] = Math.min(min[axis], item.center[axis] - half);
      max[axis] = Math.max(max[axis], item.center[axis] + half);
    });
  }));
  return { min, max };
}

/* 12 条棱：角点按 (x, y, z) 的 ± 位组合排列，只有一位不同才相邻 */
function boxSegments(item, kind, toModel) {
  const rad = ((item.rotation_z_deg || 0) * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const [cx, cy, cz] = item.center;
  const [sx, sy, sz] = item.size;
  const corners = [];
  for (const dx of [-1, 1]) {
    for (const dy of [-1, 1]) {
      for (const dz of [-1, 1]) {
        const lx = (dx * sx) / 2;
        const ly = (dy * sy) / 2;
        corners.push(toModel([
          cx + lx * cos - ly * sin,
          cy + lx * sin + ly * cos,
          cz + (dz * sz) / 2,
        ]));
      }
    }
  }
  const out = [];
  for (let i = 0; i < 8; i++) {
    for (let j = i + 1; j < 8; j++) {
      const diff = i ^ j;
      if (diff !== 1 && diff !== 2 && diff !== 4) continue;
      out.push({ a: corners[i], b: corners[j], kind });
    }
  }
  return out;
}

/* 2D 投影坐标系下的模型：x 向右、y 向上（高度）、z 朝观众（进深），
 * 整体缩放到 ±1.8 附近，与原来那个立方体的视觉尺度接近。 */
function buildModel(layout) {
  const bounds = layoutBounds(layout);
  const cx = (bounds.min[0] + bounds.max[0]) / 2;
  const cy = (bounds.min[1] + bounds.max[1]) / 2;
  const span = Math.max(bounds.max[0] - bounds.min[0], bounds.max[1] - bounds.min[1]);
  const k = 3.6 / span;
  const toModel = ([x, y, z]) => [(x - cx) * k, z * k, (y - cy) * k];
  const segments = [];
  BOX_KEYS.forEach((key) => (layout[key] || []).forEach((item) => {
    segments.push(...boxSegments(item, key, toModel));
  }));
  const footprint = HOME_FOOTPRINT.map(([x, y]) => toModel([x, y, 0]));
  return {
    segments,
    footprint,
    height: bounds.max[2] * k,
    bounds: {
      minX: Math.min(...footprint.map((p) => p[0])),
      maxX: Math.max(...footprint.map((p) => p[0])),
      minZ: Math.min(...footprint.map((p) => p[2])),
      maxZ: Math.max(...footprint.map((p) => p[2])),
    },
  };
}

export const HOME_MODEL = buildModel(HOME_LAYOUT);
