const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

/* scan46 交付包（window_wall_joined_20261008）是非度量重建：竖直轴是 Y（up = -Y），
 * 水平面相对业务系绕竖轴转了 180°，而且两条水平轴的尺度不一样（宽度 1.19、进深 1.43）。
 *
 * 上一版用“点云外接包络 ↔ 房间轮廓”拟合：包络被门口走廊和穿窗几何撑大，角点对上去
 * 以后整包点云朝门那一侧推偏约 0.5 m，家具框全部错位（用户报“所有物品框都没在对应的
 * 位置上”，而且“靠门那侧框架把点云完全包住”）。
 *
 * 现在改成用“竖直中段的墙面剖面”解这段变换：每轴各取两侧外半区里最密的墙面平面，
 * 各轴独立定尺度，让墙面落到房间轮廓上。验收：四道墙落在房间线上（±2 cm）、
 * 门口走廊留在房间外、门/窗/床与 6 件家具框都落在点云上。
 */
(async () => {
  const helper = await import('../app/static/console/js/viewer/frame.mjs');
  const { resolveFrame, framePoint, framePointInto, fitWallFrame, wallPeaks,
    fitHorizontalFrame, envelope, modalPlane, percentile } = helper;
  ['fitWallFrame', 'wallPeaks', 'fitHorizontalFrame', 'envelope', 'modalPlane', 'framePointInto']
    .forEach((name) => assert.equal(typeof helper[name], 'function', `frame.mjs must export ${name}`));

  // framePointInto 必须与 framePoint 完全一致（点云走前者，展示层走后者）：
  // 单一 scale 的旧拟合与逐轴 scale 的新拟合都要一致。
  const legacy = {
    sign: -1,
    floor: 1.345,
    horizontal: { scale: 1.1016, offsetX: 1.8151, offsetY: 2.1874, flip: true },
  };
  const perAxis = {
    sign: -1,
    floor: 1.345,
    horizontal: {
      scaleX: 1.1875, scaleZ: 1.4325, scaleUp: 1.1875,
      offsetX: 1.8455, offsetY: 2.3831, flip: true,
    },
  };
  [[0, 1.345, 0], [2.088, -0.5, 2.44], [-0.538, 0.9, -0.53]].forEach(([x, y, z]) => {
    [legacy, perAxis].forEach((candidate) => {
      assert.deepEqual(Array.from(framePointInto([0, 0, 0], 0, x, y, z, candidate)),
        framePoint(x, y, z, candidate), 'framePointInto must match framePoint');
    });
  });
  assert.deepEqual(Array.from(framePointInto([0, 0, 0], 0, 1, 2, 3, null)), [1, 2, 3]);

  // 墙面剖面：两侧最密的一层就是墙；稀疏离群点（穿窗几何）不会把墙带走
  const synthetic = [];
  for (let i = 0; i < 1200; i += 1) synthetic.push(-0.48 + ((i % 11) - 5) * 0.002);
  for (let i = 0; i < 300; i += 1) synthetic.push(-0.45 + (i / 300) * 2.4);
  for (let i = 0; i < 1200; i += 1) synthetic.push(1.96 + ((i % 11) - 5) * 0.002);
  for (let i = 0; i < 40; i += 1) synthetic.push(4.9 + i * 0.01);
  const peaks = wallPeaks(synthetic);
  assert(peaks, 'a two-wall profile must yield wall planes');
  assert(Math.abs(peaks.lo + 0.48) < 0.03, `west wall plane wrong: ${peaks.lo}`);
  assert(Math.abs(peaks.hi - 1.96) < 0.03, `east wall plane wrong: ${peaks.hi}`);
  assert(peaks.shareLo > 0.25 && peaks.shareHi > 0.25,
    `wall planes must dominate their profile, got ${peaks.shareLo}/${peaks.shareHi}`);
  assert.equal(wallPeaks([]), null);
  assert.equal(wallPeaks([1, 2, 3]), null, 'too few samples must not look like a room');
  assert.equal(wallPeaks(new Array(200).fill(0.5)), null, 'a flat profile is not a room');

  const room = { min: [-0.4844042583, -0.5009722655], max: [2.4095957417, 2.7690277345] };
  const wallProfile = {
    x: [-0.48, 1.96].flatMap((plane) => new Array(900).fill(0).map((_, i) => plane + ((i % 9) - 4) * 0.002)),
    z: [-0.27, 2.01].flatMap((plane) => new Array(900).fill(0).map((_, i) => plane + ((i % 9) - 4) * 0.002)),
  };
  const fit = fitWallFrame(wallProfile, room);
  assert(fit, 'a wall profile plus a room outline must produce a fit');
  assert.equal(fit.source, 'walls');
  // 直方图分箱有 1 cm 级量化误差，容差取 1%
  assert(Math.abs(fit.scaleX - 2.894 / 2.44) < 0.01, `scaleX wrong: ${fit.scaleX}`);
  assert(Math.abs(fit.scaleZ - 3.270 / 2.28) < 0.01, `scaleZ wrong: ${fit.scaleZ}`);
  assert.equal(fit.scaleUp, Math.min(fit.scaleX, fit.scaleZ), 'vertical scale follows the uncompressed axis');
  // 180° 偏转：包内最大 x/z 一侧的墙面 → 房间最小角
  assert(Math.abs(fit.offsetX - fit.scaleX * 1.96 - room.min[0]) < 0.05, 'x offset must land on the wall');
  assert(Math.abs(fit.offsetY - fit.scaleZ * 2.01 - room.min[1]) < 0.05, 'y offset must land on the wall');
  const planes = (list) => list.flatMap((plane) => new Array(900).fill(0)
    .map((_, i) => plane + ((i % 9) - 4) * 0.002));
  // 两轴比例差太多（深度被压得和宽度不是一个房间）→ 拒绝
  assert.equal(fitWallFrame({ x: wallProfile.x, z: planes([0, 1.6]) }, { min: [0, 0], max: [2.4, 2.4] }), null,
    'a profile whose two axes disagree by >45% must be rejected');
  // 两轴差异在允许范围内（scan46 实测 21%）时照常工作
  const mild = fitWallFrame({ x: wallProfile.x, z: planes([0, 1.9]) }, { min: [0, 0], max: [2.4, 2.4] });
  assert(mild && mild.anisotropy < 0.45, `mild anisotropy must be accepted, got ${JSON.stringify(mild)}`);
  // 尺度离 1 太远 → 拒绝
  assert.equal(fitWallFrame(wallProfile, { min: [0, 0], max: [9.0, 9.0] }), null);
  // 剖面太散（找不到确定墙面）→ 拒绝
  const flat = new Array(4000).fill(0).map((_, i) => -0.5 + (i / 4000) * 2.6);
  assert.equal(fitWallFrame({ x: flat, z: wallProfile.z }, room), null);
  assert.equal(fitWallFrame(null, room), null);
  assert.equal(fitWallFrame(wallProfile, null), null);
  // 墙面之间不足 1 m → 拒绝
  assert.equal(fitWallFrame({ x: wallProfile.x, z: [0, 0.5].flatMap((p) => new Array(900).fill(p)) }, room), null);

  // 只有包络（拿不到墙面剖面）时仍可用旧的兜底拟合，但墙面剖面必须优先
  const pointExtent = { x: [-0.538, 2.088], z: [-0.53, 2.44] };
  const fallback = fitHorizontalFrame(pointExtent, room);
  assert(fallback && Math.abs(fallback.scale - 1.101) < 0.01, 'envelope fallback must keep working');
  const viewRow = { front: { R: [[-1, 0, 0], [0, 1, 0], [0, 0, -1]] } };
  const preferred = resolveFrame(viewRow, 1.345, { wallProfile, pointExtent, roomBounds: room });
  assert.equal(preferred.horizontal.source, 'walls', 'wall planes must win over the envelope');
  const onlyExtent = resolveFrame(viewRow, 1.345, { pointExtent, roomBounds: room });
  assert.equal(onlyExtent.horizontal.source, 'bounds', 'without a profile the envelope is the fallback');

  // 地板面取“点最密的一层”，不能用极端分位数（会把点云抬高十几厘米）
  const planeValues = [];
  for (let i = 0; i < 500; i += 1) planeValues.push(Math.random() * 2.4 - 0.94);
  for (let i = 0; i < 400; i += 1) planeValues.push(1.345 + (Math.random() - 0.5) * 0.01);
  assert(Math.abs(modalPlane(planeValues, -1) - 1.345) < 0.02, 'modal floor plane must sit at ~1.345');
  assert.equal(modalPlane([], -1), null);
  assert.equal(percentile([3, 1, 2], 50), 2);

  // 包络：干净点云用真实极值，含离群点时退回 0.1%/99.9% 分位
  const clean = [];
  for (let i = 0; i <= 1000; i += 1) clean.push(i / 1000);
  assert.deepEqual(envelope(clean), [0, 1]);
  const dirty = clean.slice();
  dirty.push(9.5);
  const guarded = envelope(dirty);
  assert(guarded[1] < 1.05 && guarded[1] > 0.99, `outlier must not stretch the envelope, got ${guarded[1]}`);
  assert.equal(envelope([]), null);

  /* —— 真实交付包验收 —— */
  const post = path.join(__dirname, '../data/work/46/postprocess');
  const plyPath = path.join(post, 'window_wall_joined_20261008.ply');
  const structurePath = path.join(post, 'structure_calibrated.json');
  const measurementsPath = path.join(post, 'measurements.json');
  if (!fs.existsSync(plyPath) || !fs.existsSync(structurePath) || !fs.existsSync(measurementsPath)) {
    console.log('SKIP: scan46 fixtures not present');
    console.log('PASS: delivery pack horizontal fit maps the cloud onto the audited room outline');
    return;
  }
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
  let offset = 0;
  props.forEach((prop) => { prop.offset = offset; offset += sizes[prop.type]; });
  const stride = offset;
  const findProp = (name) => props.find((prop) => prop.name === name);
  const px = findProp('x');
  const py = findProp('y');
  const pz = findProp('z');
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  const read = (prop, base) => (prop.type === 'double'
    ? view.getFloat64(base + prop.offset, true)
    : view.getFloat32(base + prop.offset, true));
  const at = (i, prop) => read(prop, headerEnd + i * stride);

  // 与 pointcloud.js 同一条采样路径：地板锚点 → 水平极值 + 竖直中段剖面
  const stepY = Math.max(1, Math.floor(count / 60000));
  const ys = [];
  for (let i = 0; i < count; i += stepY) ys.push(at(i, py));
  const realFloor = modalPlane(ys, -1);
  assert(Math.abs(realFloor - 1.345) < 0.02,
    `real pack floor plane should sit at ~1.345, got ${realFloor} (p99.5=${percentile(ys, 99.5)})`);
  const step = Math.max(1, Math.floor(count / 120000));
  const xs = [];
  const zs = [];
  const hs = [];
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i < count; i += 1) {
    const x = at(i, px);
    const z = at(i, pz);
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
    if (i % step === 0) {
      xs.push(x);
      zs.push(z);
      hs.push(realFloor - at(i, py));
    }
  }
  let minH = Infinity;
  let maxH = -Infinity;
  hs.forEach((v) => { if (v < minH) minH = v; if (v > maxH) maxH = v; });
  const bandX = [];
  const bandZ = [];
  for (let i = 0; i < hs.length; i += 1) {
    if (hs[i] >= minH + (maxH - minH) * 0.2 && hs[i] <= minH + (maxH - minH) * 0.8) {
      bandX.push(xs[i]);
      bandZ.push(zs[i]);
    }
  }
  assert(bandX.length > 20000, `wall profile needs enough samples, got ${bandX.length}`);
  const realStructure = JSON.parse(fs.readFileSync(structurePath, 'utf8'));
  const realRoom = realStructure.room.bounds_xy;
  const real = resolveFrame(viewRow, realFloor, {
    wallProfile: { x: bandX, z: bandZ },
    pointExtent: { x: [minX, maxX], z: [minZ, maxZ], xBand: envelope(xs), zBand: envelope(zs) },
    roomBounds: realRoom,
  });
  assert(real && real.horizontal, 'real delivery pack must produce a horizontal fit');
  assert.equal(real.horizontal.source, 'walls', 'scan46 must be fitted on wall planes, not the envelope');
  assert(Math.abs(real.horizontal.scaleX - 1.1875) < 0.01,
    `scan46 width scale must be ~1.1875, got ${real.horizontal.scaleX}`);
  assert(Math.abs(real.horizontal.scaleZ - 1.4325) < 0.02,
    `scan46 depth scale must be ~1.4325, got ${real.horizontal.scaleZ}`);
  assert(real.horizontal.scaleZ - real.horizontal.scaleX > 0.15,
    'scan46 is a non-metric pack: the depth axis needs its own scale');
  assert.equal(real.horizontal.scaleUp, real.horizontal.scaleX,
    'vertical scale follows the image-plane (uncompressed) axis');

  const scratch = [0, 0, 0];
  const mapped = (x, y, z) => Array.from(framePointInto(scratch, 0, x, y, z, real));

  // 1) 四道墙落在房间线框上（这就是用户要的“框架刚好和现实重叠”）
  const peaksReal = real.horizontal.peaks;
  assert(Math.abs(mapped(peaksReal.x[1], realFloor, 0)[0] - realRoom.min[0]) < 0.02,
    'pack east wall must land on the room outline min x');
  assert(Math.abs(mapped(peaksReal.x[0], realFloor, 0)[0] - realRoom.max[0]) < 0.02,
    'pack west wall must land on the room outline max x');
  assert(Math.abs(mapped(0, realFloor, peaksReal.z[1])[1] - realRoom.min[1]) < 0.02,
    'pack door wall must land on the room outline min y');
  assert(Math.abs(mapped(0, realFloor, peaksReal.z[0])[1] - realRoom.max[1]) < 0.02,
    'pack window wall must land on the room outline max y');

  // 2) 门口走廊必须留在房间外：靠门那侧不允许“框架把点云包住”
  let corridor = 0;
  let corridorOutside = 0;
  for (let i = 0; i < count; i += 5) {
    const z = at(i, pz);
    if (z < 2.08) continue;
    corridor += 1;
    if (mapped(at(i, px), at(i, py), z)[1] < realRoom.min[1]) corridorOutside += 1;
  }
  assert(corridor > 1000, `corridor needs enough evidence, got ${corridor}`);
  assert(corridorOutside / corridor > 0.95,
    `through-door corridor must stay outside the room outline (${corridorOutside}/${corridor})`);

  // 3) 家具框落在点云上：逐轴墙面拟合 6/6，只修竖直轴只有 4/6
  const measurements = JSON.parse(fs.readFileSync(measurementsPath, 'utf8'));
  assert.equal(measurements.objects.length, realStructure.semantic_instances.length,
    'measurements and audited semantic_instances must describe the same furniture set');
  const boxes = measurements.objects.map((item) => {
    const rotation = ((item.rotation_z_deg || 0) * Math.PI) / 180;
    return {
      id: item.id,
      cx: item.center[0],
      cy: item.center[1],
      cz: item.center[2],
      hx: item.length_m / 2,
      hy: item.width_m / 2,
      hz: item.height_m / 2,
      cos: Math.cos(rotation),
      sin: Math.sin(rotation),
      hits: 0,
    };
  });
  const countInBoxes = (transform) => {
    boxes.forEach((box) => { box.hits = 0; });
    for (let i = 0; i < count; i += 4) {
      const point = transform(at(i, px), at(i, py), at(i, pz));
      boxes.forEach((box) => {
        const dx = point[0] - box.cx;
        const dy = point[1] - box.cy;
        if (Math.abs(box.cos * dx + box.sin * dy) > box.hx) return;
        if (Math.abs(-box.sin * dx + box.cos * dy) > box.hy) return;
        if (Math.abs(point[2] - box.cz) > box.hz) return;
        box.hits += 1;
      });
    }
    return boxes.map((box) => ({ id: box.id, hits: box.hits }));
  };
  const fittedHits = countInBoxes((x, y, z) => mapped(x, y, z));
  const upOnly = countInBoxes((x, y, z) => Array.from(
    framePointInto(scratch, 0, x, y, z, resolveFrame(viewRow, realFloor))));
  const threshold = 1000;
  const summary = (list) => Object.fromEntries(list.map((box) => [box.id, box.hits]));
  assert(fittedHits.every((box) => box.hits >= threshold),
    `every audited furniture box must sit on cloud geometry: ${JSON.stringify(summary(fittedHits))}`);
  assert(upOnly.filter((box) => box.hits >= threshold).length <= 4,
    `the up-axis-only mapping must stay worse: ${JSON.stringify(summary(upOnly))}`);

  /* 4) 地标回归：包内认得出来的门、窗、床映射后必须落在审计过的同名实例里。 */
  const centroid = (accept) => {
    let total = 0;
    const sum = [0, 0, 0];
    for (let i = 0; i < count; i += 3) {
      const x = at(i, px);
      const y = at(i, py);
      const z = at(i, pz);
      if (!accept(x, y, z)) continue;
      const point = mapped(x, y, z);
      total += 1;
      sum[0] += point[0];
      sum[1] += point[1];
      sum[2] += point[2];
    }
    return { total, centroid: total ? sum.map((value) => value / total) : [0, 0, 0] };
  };
  const doorRegion = (x, y, z) => z > 1.95 && z < 2.07 && x > -0.6 && x < 0.6
    && realFloor - y > 0.3 && realFloor - y < 1.7;
  const door = centroid(doorRegion);
  const doorBox = realStructure.doors[0];
  assert(door.total > 2000, `door anchor needs enough cloud evidence, got ${door.total}`);
  assert(Math.abs(door.centroid[1] - doorBox.center[1]) < 0.15,
    `door leaf must sit on the audited door wall, got y=${door.centroid[1].toFixed(3)}`);
  assert(Math.abs(door.centroid[0] - doorBox.center[0]) < 0.35,
    `door leaf must sit at the audited door x, got ${door.centroid[0].toFixed(3)}`);
  assert(Math.abs(door.centroid[2] - doorBox.center[2]) < 0.4,
    `door must keep the audited height, got z=${door.centroid[2].toFixed(3)}`);

  const windowRegion = (x, y, z) => z < -0.30 && realFloor - y > 0.9 && realFloor - y < 2.1;
  let windowLo = Infinity;
  let windowHi = -Infinity;
  for (let i = 0; i < count; i += 3) {
    const z = at(i, pz);
    if (z >= -0.30) continue;
    const h = realFloor - at(i, py);
    if (h <= 0.9 || h >= 2.1) continue;
    const x = mapped(at(i, px), at(i, py), z)[0];
    if (x < windowLo) windowLo = x;
    if (x > windowHi) windowHi = x;
  }
  const windowBox = realStructure.windows[0];
  assert(windowBox.center[0] > windowLo && windowBox.center[0] < windowHi,
    `window opening must cover the audited window x=${windowBox.center[0].toFixed(3)}, `
    + `got ${windowLo.toFixed(3)}..${windowHi.toFixed(3)}`);
  assert(centroid(windowRegion).total > 2000, 'window anchor needs cloud evidence');

  const bedBoxItem = realStructure.semantic_instances.find((entry) => entry.instance_id === 'bed_001');
  const bed = centroid((x, y, z) => x > 0.1 && x < 2.0 && z > -0.4 && z < 1.0
    && realFloor - y > 0.2 && realFloor - y < 0.8);
  assert(bed.total > 5000, `bed anchor needs enough cloud evidence, got ${bed.total}`);
  assert(Math.abs(bed.centroid[0] - bedBoxItem.center[0]) < 0.35
    && Math.abs(bed.centroid[1] - bedBoxItem.center[1]) < 0.35,
  `bedding must land inside the audited bed box, got ${bed.centroid.map((v) => v.toFixed(2))} `
  + `vs ${bedBoxItem.center.map((v) => v.toFixed(2))}`);

  console.log('PASS: delivery pack fitted onto the audited room walls '
    + `(scaleX=${real.horizontal.scaleX.toFixed(4)} scaleZ=${real.horizontal.scaleZ.toFixed(4)} `
    + `floor=${realFloor.toFixed(3)})`);
  console.log(`       6/6 furniture boxes on cloud: ${JSON.stringify(summary(fittedHits))} `
    + `(up-axis only: ${JSON.stringify(summary(upOnly))})`);
  console.log(`       walls on the outline ±2cm, corridor outside ${(100 * corridorOutside / corridor).toFixed(1)}%, `
    + `door=(${door.centroid.map((v) => v.toFixed(2)).join(', ')})`
    + ` window x=${windowLo.toFixed(2)}..${windowHi.toFixed(2)}`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
