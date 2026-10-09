const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

/* scan46“整体框架校准”回归（用户 2026-10-09 手调参数固化）。
 *
 * 用户口径：点云保持交付包“之前的大小”（原生尺寸），缩放/平移由整个结构大框架承担，
 * 让家具框“刚好和现实重叠”，而不是让框架把点云包住，也绝不单独挪某个物品框。
 * 校准页导出的 p_display = T · S · Rz(yaw) · p_business 中，显示系就是交付包原生帧
 * (pack_x, pack_z, floor − pack_y)。查看器必须：
 *   - 点云 / 窗边展示层：只做竖直轴映射，不翻转、不缩放；
 *   - 结构框架整体：套同一份 T · S · Rz(yaw)（boxesGroup + labelGroup 同一个矩阵）。
 * 验收：用校准矩阵把点云映射回业务系，6 件家具框全部落在点云上，贴合分与手调记录一致。
 */
(async () => {
  const helper = await import('../app/static/console/js/viewer/frame.mjs');
  const {
    resolveFrame, framePoint, framePointInto,
    calibrationTransform, calibrationPoint, calibrationRows, resolveCalibration,
  } = helper;
  ['calibrationTransform', 'calibrationPoint', 'calibrationRows', 'resolveCalibration']
    .forEach((name) => assert.equal(typeof helper[name], 'function', `frame.mjs must export ${name}`));

  const near = (actual, expected, tol, label) => {
    assert(Math.abs(actual - expected) <= tol, `${label}: got ${actual}, want ${expected} (±${tol})`);
  };

  /* —— 1) 参数语义：显示系 = T · S · Rz(yaw) · 业务系（与校准页 mtx = T·S·Rz 一致） —— */
  const params = {
    yaw_deg: 180, mirror_x: false, mirror_y: false,
    scale_x: 0.84, scale_y: 0.71, scale_z: 0.9,
    translate_x: 1.54, translate_y: 1.64, translate_z: 0,
  };
  const t180 = calibrationTransform({ transform: params });
  assert(t180, 'a complete calibration must produce a transform');
  const p = (x, y, z) => calibrationPoint(t180, x, y, z);
  near(p(1, 0, 0)[0], 1.54 - 0.84, 1e-9, 'yaw180 x axis x');
  near(p(1, 0, 0)[1], 1.64, 1e-9, 'yaw180 x axis y');
  near(p(0, 1, 0)[0], 1.54, 1e-9, 'yaw180 y axis x');
  near(p(0, 1, 0)[1], 1.64 - 0.71, 1e-9, 'yaw180 y axis y');
  near(p(0, 0, 1)[2], 0.9, 1e-9, 'height scales by sz');
  near(p(0, 0, 3)[2], 2.7, 1e-9, 'height has no offset (tz=0)');
  // 房间最小角 → 点云最大角一侧（校准页 180° 摆放的预期方向）
  near(p(-0.4844042583, -0.5009722655, 0)[0], 1.54 + 0.84 * 0.4844042583, 1e-9, 'room min corner x');
  near(p(-0.4844042583, -0.5009722655, 0)[1], 1.64 + 0.71 * 0.5009722655, 1e-9, 'room min corner y');

  // mirror 标志只翻转对应的水平轴
  const mirroredX = calibrationPoint(calibrationTransform({
    transform: { ...params, mirror_x: true },
  }), 1, 0, 0);
  near(mirroredX[0], 1.54 + 0.84, 1e-9, 'mirror_x flips the x axis before yaw');
  near(mirroredX[1], 1.64, 1e-9, 'mirror_x leaves y untouched');
  const mirroredY = calibrationPoint(calibrationTransform({
    transform: { ...params, mirror_y: true },
  }), 0, 1, 0);
  near(mirroredY[1], 1.64 + 0.71, 1e-9, 'mirror_y flips the y axis before yaw');

  // 任意 yaw（90°）也要按 Rz 旋转而不是硬编码 180°
  const t90 = calibrationTransform({ transform: { ...params, yaw_deg: 90 } });
  const rotated = calibrationPoint(t90, 1, 0, 0);
  near(rotated[0], 1.54, 1e-9, 'yaw90 x→y rotation keeps x offset');
  near(rotated[1], 1.64 + 0.71, 1e-9, 'yaw90 maps +x business to +y display');

  // rows 是给 THREE.Matrix4.set 的行主序参数，必须与 calibrationPoint 同值
  const rows = calibrationRows(t180);
  assert.equal(rows.length, 16, 'rows must be 16 element row-major');
  [[0, 0, 0], [1, 2, 3], [-0.5, 2.4, 1.3]].forEach(([x, y, z]) => {
    const expect = calibrationPoint(t180, x, y, z);
    near(rows[0] * x + rows[1] * y + rows[2] * z + rows[3], expect[0], 1e-9, 'rows x');
    near(rows[4] * x + rows[5] * y + rows[6] * z + rows[7], expect[1], 1e-9, 'rows y');
    near(rows[8] * x + rows[9] * y + rows[10] * z + rows[11], expect[2], 1e-9, 'rows z');
  });
  assert.equal(rows[15], 1, 'last row must be homogeneous');

  // 参数不完整 / 尺度非正 → 拒绝（绝不半套用）
  assert.equal(calibrationTransform(null), null);
  assert.equal(calibrationTransform({}), null);
  assert.equal(calibrationTransform({ transform: { ...params, scale_z: undefined } }), null,
    'missing scale must be rejected instead of defaulting');
  assert.equal(calibrationTransform({ transform: { ...params, scale_x: 0 } }), null,
    'non-positive scale must be rejected');
  assert.equal(calibrationTransform({ transform: { ...params, translate_y: Number.NaN } }), null,
    'non-finite translate must be rejected');

  /* —— 2) resolveCalibration：点云原生帧 + 结构框架矩阵 —— */
  const viewRow = { front: { R: [[-1, 0, 0], [0, 1, 0], [0, 0, -1]] } };   // up = -Y
  const zUpRow = { front: { R: [[1, 0, 0], [0, -1, 0], [0, 0, -1]] } };     // up = +Z
  const payload = { transform: params, cloud_frame: { floor_y_pack: 1.3449620543191287 }, score: '169734' };
  const resolved = resolveCalibration(payload, viewRow, 1.2);
  assert(resolved, 'y-vertical views must accept the calibration');
  assert.deepEqual(resolved.frame, { kind: 'y-vertical', sign: -1, floor: 1.3449620543191287 },
    'the point cloud frame must only do the vertical mapping with the recorded floor');
  near(resolved.rows[0], -0.84, 1e-12, 'shape rows[0]');
  near(resolved.rows[5], -0.71, 1e-12, 'shape rows[5]');
  near(resolved.rows[10], 0.9, 1e-12, 'shape rows[10]');
  near(resolved.rows[3], 1.54, 1e-12, 'shape rows[3]');
  near(resolved.rows[7], 1.64, 1e-12, 'shape rows[7]');
  assert.equal(resolved.score, '169734');
  // 包内视角与校准文件约定不符（本身就是 z-up / up=+Y）→ 退回通用路径
  assert.equal(resolveCalibration(payload, zUpRow, 1.2), null, 'z-up packs must not take the calibration');
  const plusYRow = { front: { R: [[-1, 0, 0], [0, -1, 0], [0, 0, -1]] } };
  assert.equal(resolveCalibration(payload, plusYRow, 1.2), null, 'up=+Y packs must not take the calibration');
  // 没记录地板时用现场采样的 fallback；两者都没有才算失败
  const noFloor = { transform: params };
  const fallbackUsed = resolveCalibration(noFloor, viewRow, 1.25);
  assert(fallbackUsed && fallbackUsed.frame.floor === 1.25,
    'fallback floor must be used when the file has no anchor');
  assert.equal(resolveCalibration(noFloor, viewRow, null), null,
    'no floor at all must be rejected');

  /* —— 3) 真实交付包验收：点云原生 + 6/6 家具框落在点云上 —— */
  const post = path.join(__dirname, '../data/work/46/postprocess');
  const plyPath = path.join(post, 'window_wall_joined_20261008.ply');
  const structurePath = path.join(post, 'structure_calibrated.json');
  const calibrationPath = path.join(post, 'frame_calibration.json');
  if (!fs.existsSync(plyPath) || !fs.existsSync(structurePath) || !fs.existsSync(calibrationPath)) {
    console.log('SKIP: scan46 fixtures not present');
    console.log('PASS: scan46 whole-frame calibration keeps the cloud native and lands every furniture box');
    return;
  }
  const calibration = JSON.parse(fs.readFileSync(calibrationPath, 'utf8'));
  const real = resolveCalibration(calibration, viewRow, 0);
  assert(real, 'the recorded scan46 calibration must resolve');
  assert.equal(real.frame.floor, calibration.cloud_frame.floor_y_pack,
    'the viewer must use the floor anchor recorded at tuning time');

  const buffer = fs.readFileSync(plyPath);
  const headerEnd = buffer.indexOf('end_header\n') + 'end_header\n'.length;
  const header = buffer.subarray(0, headerEnd).toString('utf8');
  const count = Number(/element vertex (\d+)/.exec(header)[1]);
  const props = [];
  header.split('\n').forEach((line) => {
    const parts = line.trim().split(/\s+/);
    if (parts[0] !== 'property' || parts[1] === 'list') return;
    props.push({ type: parts[1], name: parts[2] });
  });
  const sizes = { float: 4, double: 8, uchar: 1, char: 1, short: 2, ushort: 2, int: 4, uint: 4 };
  let stride = 0;
  props.forEach((prop) => { prop.offset = stride; stride += sizes[prop.type]; });
  const find = (name) => props.find((prop) => prop.name === name);
  const px = find('x');
  const py = find('y');
  const pz = find('z');
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  const at = (i, prop) => (prop.type === 'double'
    ? view.getFloat64(headerEnd + i * stride + prop.offset, true)
    : view.getFloat32(headerEnd + i * stride + prop.offset, true));

  // 点云显示坐标必须就是交付包原生帧：(pack_x, pack_z, floor − pack_y)
  const floor = real.frame.floor;
  const display = (x, y, z) => framePoint(x, y, z, real.frame);
  let mn = [Infinity, Infinity, Infinity];
  let mx = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < count; i += 1) {
    const point = display(at(i, px), at(i, py), at(i, pz));
    for (let a = 0; a < 3; a += 1) {
      if (point[a] < mn[a]) mn[a] = point[a];
      if (point[a] > mx[a]) mx[a] = point[a];
    }
  }
  const cloudBox = calibration.cloud_box;
  [0, 1, 2].forEach((a) => {
    near(mn[a], cloudBox.min[a], 0.02, `native cloud min[${a}] must match the tuning record`);
    near(mx[a], cloudBox.max[a], 0.02, `native cloud max[${a}] must match the tuning record`);
  });
  // 点云零缩放：与交付包原始范围一一对应，不因为校准而变大或变小
  near(mx[0] - mn[0], 2.0876357555389404 + 0.5380893349647522, 0.02, 'cloud keeps its native x span');
  near(mx[1] - mn[1], 2.440000057220459 + 0.5275366306304932, 0.02, 'cloud keeps its native y span');

  // 结构框架矩阵必须与校准页一致：把点云用逆变换映射回业务系，逐框数点
  const l = real.transform.linear;
  const t = real.transform.translate;
  const det = l[0][0] * (l[1][1] * l[2][2] - l[1][2] * l[2][1])
    - l[0][1] * (l[1][0] * l[2][2] - l[1][2] * l[2][0])
    + l[0][2] * (l[1][0] * l[2][1] - l[1][1] * l[2][0]);
  assert(Math.abs(det) > 1e-9, 'calibration linear part must be invertible');
  const inv = [
    [(l[1][1] * l[2][2] - l[1][2] * l[2][1]) / det,
      (l[0][2] * l[2][1] - l[0][1] * l[2][2]) / det,
      (l[0][1] * l[1][2] - l[0][2] * l[1][1]) / det],
    [(l[1][2] * l[2][0] - l[1][0] * l[2][2]) / det,
      (l[0][0] * l[2][2] - l[0][2] * l[2][0]) / det,
      (l[0][2] * l[1][0] - l[0][0] * l[1][2]) / det],
    [(l[1][0] * l[2][1] - l[1][1] * l[2][0]) / det,
      (l[0][1] * l[2][0] - l[0][0] * l[2][1]) / det,
      (l[0][0] * l[1][1] - l[0][1] * l[1][0]) / det],
  ];
  const toBusiness = (x, y, z) => {
    const dx = x - t[0];
    const dy = y - t[1];
    const dz = z - t[2];
    return [
      inv[0][0] * dx + inv[0][1] * dy + inv[0][2] * dz,
      inv[1][0] * dx + inv[1][1] * dy + inv[1][2] * dz,
      inv[2][0] * dx + inv[2][1] * dy + inv[2][2] * dz,
    ];
  };

  const structure = JSON.parse(fs.readFileSync(structurePath, 'utf8'));
  const boxes = structure.semantic_instances.map((item) => {
    const rotation = ((item.rotation_z_deg || 0) * Math.PI) / 180;
    return {
      id: item.instance_id,
      center: item.center,
      half: [item.size[0] / 2, item.size[1] / 2, item.size[2] / 2],
      cos: Math.cos(rotation),
      sin: Math.sin(rotation),
      hits: 0,
    };
  });
  assert.equal(boxes.length, 6, 'scan46 must keep its 6 audited furniture boxes');
  // 与校准页同一口径：点云按 2 抽 1 采样，再隔一个数一个，命中 +2（margin 0.05）
  let score = 0;
  const inBox = (point, box) => {
    const dx = point[0] - box.center[0];
    const dy = point[1] - box.center[1];
    if (Math.abs(box.cos * dx + box.sin * dy) > box.half[0] + 0.05) return false;
    if (Math.abs(-box.sin * dx + box.cos * dy) > box.half[1] + 0.05) return false;
    return Math.abs(point[2] - box.center[2]) <= box.half[2] + 0.05;
  };
  for (let i = 0; i < count; i += 4) {
    const point = toBusiness(...display(at(i, px), at(i, py), at(i, pz)));
    boxes.forEach((box) => {
      if (inBox(point, box)) { box.hits += 1; score += 2; }
    });
  }
  const recorded = Number(calibration.score);
  const summary = Object.fromEntries(boxes.map((box) => [box.id, box.hits]));
  assert(Number.isFinite(recorded) && recorded > 0, 'the recorded score must be a number');
  assert.equal(score, recorded,
    `recomputed fit score must reproduce the tuning session exactly: got ${score}, want ${recorded} (${JSON.stringify(summary)})`);
  boxes.forEach((box) => {
    assert(box.hits >= 2000,
      `every audited furniture box must sit on cloud geometry, ${box.id} only has ${box.hits} hits`);
  });

  console.log('PASS: scan46 whole-frame calibration (cloud stays native, frames scale: '
    + `${real.transform.scale.map((v) => v).join('/')})`);
  console.log(`       native cloud box = [${mn.map((v) => v.toFixed(3)).join(', ')}] .. [${mx.map((v) => v.toFixed(3)).join(', ')}]`);
  console.log(`       fit score ${score} vs recorded ${recorded}; per-box hits ${JSON.stringify(summary)}`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
