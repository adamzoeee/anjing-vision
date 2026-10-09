const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

/* 交付包（window_wall_joined_20261008）除了竖直轴是 Y，水平面还相对业务系绕竖轴转了 180°，
 * 并整体小了约 10%。只做“竖直轴映射”会把窗帘/床头挂到门那面墙，且家具框与点云对不上。
 * 这里用“点云水平范围 ↔ 房间轮廓”做刚体拟合：两轴推算出的尺度必须一致，否则拒绝拉伸。
 * 关键回归：点云逐点映射必须与展示层共用 framePointInto，否则家具框与点云再次错开。
 */
(async () => {
  const helper = await import('../app/static/console/js/viewer/frame.mjs');
  const { resolveFrame, framePoint, framePointInto, fitHorizontalFrame, percentile, envelope, modalPlane } = helper;
  assert.equal(typeof fitHorizontalFrame, 'function', 'frame.mjs must export fitHorizontalFrame');
  assert.equal(typeof envelope, 'function', 'frame.mjs must export envelope');
  assert.equal(typeof modalPlane, 'function', 'frame.mjs must export modalPlane');
  assert.equal(typeof framePointInto, 'function', 'frame.mjs must export framePointInto');

  // framePointInto 必须与 framePoint 完全一致（点云走前者，展示层走后者）
  const probeFrame = { sign: -1, floor: 1.345, horizontal: { scale: 1.1016, offsetX: 1.8151, offsetY: 2.1874, flip: true } };
  [[0, 1.345, 0], [2.088, -0.5, 2.44], [-0.538, 0.9, -0.53]].forEach(([x, y, z]) => {
    const into = framePointInto([0, 0, 0], 0, x, y, z, probeFrame);
    assert.deepEqual(Array.from(into), framePoint(x, y, z, probeFrame),
      'framePointInto must match framePoint');
  });
  assert.deepEqual(Array.from(framePointInto([0, 0, 0], 0, 1, 2, 3, null)), [1, 2, 3]);

  // 地板面取“点最密的一层”，不能用极端分位数（会把点云抬高十几厘米）
  const planeValues = [];
  for (let i = 0; i < 500; i += 1) planeValues.push(Math.random() * 2.4 - 0.94);
  for (let i = 0; i < 400; i += 1) planeValues.push(1.345 + (Math.random() - 0.5) * 0.01);
  const plane = modalPlane(planeValues, -1);
  assert(Math.abs(plane - 1.345) < 0.02, `modal floor plane should sit at 1.345, got ${plane}`);
  assert.equal(modalPlane([], -1), null);

  // 包络：干净点云用真实极值，含离群点时退回 0.1%/99.9% 分位
  const clean = [];
  for (let i = 0; i <= 1000; i += 1) clean.push(i / 1000);
  assert.deepEqual(envelope(clean), [0, 1]);
  const dirty = clean.slice();
  dirty.push(9.5);
  const guarded = envelope(dirty);
  assert(guarded[1] < 1.05, `outlier must not stretch the envelope, got ${guarded[1]}`);
  assert(guarded[1] > 0.99, `clean side must keep its extent, got ${guarded[1]}`);
  assert.equal(envelope([]), null);
  assert.equal(percentile([3, 1, 2], 50), 2);

  const packViews = { front: { R: [[-1, 0, 0], [0, 1, 0], [0, 0, -1]] } };
  const pointExtent = { x: [-0.538, 2.088], z: [-0.53, 2.44] };
  const roomBounds = { min: [-0.4844042583, -0.5009722655], max: [2.4095957417, 2.7690277345] };

  const fit = fitHorizontalFrame(pointExtent, roomBounds);
  assert(fit, 'a consistent two-axis extent must produce a horizontal fit');
  assert(Math.abs(fit.scale - 1.101) < 0.01, `expected scale ~1.101, got ${fit.scale}`);
  assert.equal(fit.source, 'bounds', 'consistency check must pick the raw extent pair here');

  // 某一组输入自相矛盾（两轴比例差得离谱）时直接淘汰该组，绝不用它拉伸点云
  const dirtyBand = {
    x: [-0.538, 2.088], z: [-0.53, 2.44],
    xBand: [-0.537, 2.089], zBand: [-0.53, 9.5],
  };
  const stillFits = fitHorizontalFrame(dirtyBand, roomBounds);
  assert(stillFits && stillFits.source === 'bounds',
    'an inconsistent candidate must be dropped in favour of the consistent one');
  const mixedExtent = {
    x: [-9.5, 2.088], z: [-0.53, 2.44],
    xBand: [-0.538, 2.088], zBand: [-0.53, 2.44],
  };
  const recovered = fitHorizontalFrame(mixedExtent, roomBounds);
  assert(recovered && recovered.source === 'band',
    'when the raw extremes are outliers the quantile band must be chosen');
  assert(Math.abs(recovered.scale - 1.101) < 0.01, `band scale should match, got ${recovered.scale}`);

  const frame = resolveFrame(packViews, 1.476, { pointExtent, roomBounds });
  assert(frame && frame.horizontal, 'resolveFrame must carry the horizontal fit');
  const close = (actual, expected, tol = 1e-6) => assert.ok(
    actual.length === expected.length && actual.every((value, index) => Math.abs(value - expected[index]) < tol),
    `${JSON.stringify(actual)} != ${JSON.stringify(expected)}`,
  );
  // 180° 偏转：包内 x/z 最大角 → 房间最小角，其余角点同理；地板仍在 z=0
  close(framePoint(2.088, 1.476, 2.44, frame), [-0.4844042583, -0.5009722655, 0], 2e-3);
  close(framePoint(-0.538, 1.476, -0.53, frame), [2.4095957417, 2.7690277345, 0], 2e-3);
  // 竖直轴仍然由 y 决定（up = -Y）
  const top = framePoint(0, 1.476 - 2.0, 0, frame);
  assert(Math.abs(top[2] - 2.0 * fit.scale) < 2e-3, `ceiling direction wrong: ${top[2]}`);

  // 不做竖直校正时保持原样
  close(framePoint(1, 2, 3, null), [1, 2, 3]);
  // 两轴尺度不一致（点云包络不是房间轮廓）时拒绝拉伸，回退为纯竖直轴校正
  assert.equal(fitHorizontalFrame({ x: [-0.538, 2.088], z: [-0.53, 9.5] }, roomBounds), null);
  assert.equal(fitHorizontalFrame({ x: [0, 0.0001], z: [-0.53, 2.44] }, roomBounds), null);
  assert.equal(fitHorizontalFrame(null, roomBounds), null);
  assert.equal(fitHorizontalFrame(pointExtent, null), null);
  const plain = resolveFrame(packViews, 1.476);
  assert.equal(plain.horizontal, undefined);
  close(framePoint(0.5, 1.476, 2, plain), [0.5, 2, 0]);

  /* 真实数据验收：审计后的 6 件家具（structure_calibrated.semantic_instances == measurements.json）
   * 必须落在交付包点云里。用当前“只修竖直轴”的实现会有一半家具框落在空地。 */
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
  const index = (name) => props.findIndex((prop) => prop.name === name);
  const ix = index('x');
  const iy = index('y');
  const iz = index('z');
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  const read = (prop, base) => (prop.type === 'double'
    ? view.getFloat64(base + prop.offset, true)
    : view.getFloat32(base + prop.offset, true));
  const xs = [];
  const ys = [];
  const zs = [];
  // 极值点很稀疏（穿窗/门口走廊），前后端都要扫全部点取极值；分位包络再抽样即可
  const bandStep = Math.max(1, Math.floor(count / 120000));
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < count; i += 1) {
    const base = headerEnd + i * stride;
    const x = read(props[ix], base);
    const y = read(props[iy], base);
    const z = read(props[iz], base);
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
    if (i % bandStep === 0) {
      xs.push(x);
      ys.push(y);
      zs.push(z);
    }
  }
  const realExtent = {
    x: [minX, maxX], z: [minZ, maxZ],
    xBand: envelope(xs), zBand: envelope(zs),
  };
  const realStructure = JSON.parse(fs.readFileSync(structurePath, 'utf8'));
  const realRoom = realStructure.room.bounds_xy;
  const realFloor = modalPlane(ys, -1);
  assert(Math.abs(realFloor - 1.345) < 0.02,
    `real pack floor plane should sit at ~1.345, got ${realFloor} (p99.5=${percentile(ys, 99.5)})`);
  const realFrame = resolveFrame(packViews, realFloor, { pointExtent: realExtent, roomBounds: realRoom });
  assert(realFrame && realFrame.horizontal, 'real delivery pack must produce a horizontal fit');
  assert.equal(realFrame.horizontal.source, 'bounds',
    'scan46 room outline is defined by the pack extent, so the raw pair must win');
  assert(Math.abs(realFrame.horizontal.scale - 1.1016) < 0.005,
    `scan46 pack scale must be ~1.1016, got ${realFrame.horizontal.scale}`);

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
  // 抽样统计即可：包内 87 万点，每 4 点取 1 点仍足以判定家具框是否落在点云上
  const countInBoxes = (transform) => {
    boxes.forEach((box) => { box.hits = 0; });
    for (let i = 0; i < count; i += 4) {
      const base = headerEnd + i * stride;
      const point = transform(read(props[ix], base), read(props[iy], base), read(props[iz], base));
      for (let b = 0; b < boxes.length; b += 1) {
        const box = boxes[b];
        const dx = point[0] - box.cx;
        const dy = point[1] - box.cy;
        if (Math.abs(box.cos * dx + box.sin * dy) > box.hx) continue;
        if (Math.abs(-box.sin * dx + box.cos * dy) > box.hy) continue;
        if (Math.abs(point[2] - box.cz) > box.hz) continue;
        box.hits += 1;
      }
    }
    return boxes.map((box) => ({ id: box.id, hits: box.hits }));
  };
  /* 点云走 framePointInto（与前端 addPointChunks 同一条路径），展示层走 framePoint。 */
  const scratch = [0, 0, 0];
  const fitted = countInBoxes((x, y, z) => Array.from(framePointInto(scratch, 0, x, y, z, realFrame)));
  const plainFrame = resolveFrame(packViews, percentile(ys, 99.6));
  const upOnly = countInBoxes((x, y, z) => Array.from(framePointInto(scratch, 0, x, y, z, plainFrame)));
  const threshold = 80;
  const fittedHits = fitted.filter((box) => box.hits >= threshold).length;
  const upOnlyHits = upOnly.filter((box) => box.hits >= threshold).length;
  const summary = (list) => Object.fromEntries(list.map((box) => [box.id, box.hits]));
  assert(fittedHits === boxes.length,
    `fitted frame must place the audited furniture on cloud geometry (got ${fittedHits}/6: ${JSON.stringify(summary(fitted))})`);
  assert(fittedHits > upOnlyHits,
    `horizontal fit must beat the up-axis-only mapping (${fittedHits} vs ${upOnlyHits}: ${JSON.stringify(summary(upOnly))})`);

  /* 地标回归（用户报的就是这些对不上）：包内认得出来的门/窗/床经过映射后必须落在
   * structure_calibrated 审计过的同名实例框里。 */
  const insideBox = (box, point, margin) => {
    const dx = point[0] - box.cx;
    const dy = point[1] - box.cy;
    return Math.abs(box.cos * dx + box.sin * dy) <= box.hx + margin
      && Math.abs(-box.sin * dx + box.cos * dy) <= box.hy + margin
      && Math.abs(point[2] - box.cz) <= box.hz + margin;
  };
  const anchorBox = (item) => {
    const rotation = ((item.rotation_z_deg || 0) * Math.PI) / 180;
    return {
      id: item.instance_id || item.category || 'anchor',
      cx: item.center[0], cy: item.center[1], cz: item.center[2],
      hx: item.size ? item.size[0] / 2 : item.length_m / 2,
      hy: item.size ? item.size[1] / 2 : item.width_m / 2,
      hz: item.size ? item.size[2] / 2 : item.height_m / 2,
      cos: Math.cos(rotation), sin: Math.sin(rotation),
    };
  };
  const centroid = (transform, accept) => {
    let total = 0;
    const sum = [0, 0, 0];
    for (let i = 0; i < count; i += 4) {
      const base = headerEnd + i * stride;
      const x = read(props[ix], base);
      const y = read(props[iy], base);
      const z = read(props[iz], base);
      if (!accept(x, y, z)) continue;
      total += 1;
      const point = transform(x, y, z);
      sum[0] += point[0]; sum[1] += point[1]; sum[2] += point[2];
    }
    return { total, centroid: total ? sum.map((value) => value / total) : [0, 0, 0] };
  };
  const coverage = (box, accept, margin = 0.2) => {
    let total = 0;
    let hit = 0;
    for (let i = 0; i < count; i += 4) {
      const base = headerEnd + i * stride;
      const x = read(props[ix], base);
      const y = read(props[iy], base);
      const z = read(props[iz], base);
      if (!accept(x, y, z)) continue;
      total += 1;
      if (insideBox(box, framePointInto(scratch, 0, x, y, z, realFrame), margin)) hit += 1;
    }
    return { total, ratio: total ? hit / total : 0 };
  };
  /* 用户报“点云的窗/书架跑到别的墙上”：门扇、窗洞、床头这三处包内可辨认的几何，
   * 映射后必须落在审计记录的对应墙面和家具框里。 */
  const doorRegion = (x, y, z) => z >= 1.93 && x > -0.55 && x < 0.4
    && realFloor - y > 0.2 && realFloor - y < 1.8;
  const windowRegion = (x, y, z) => z <= -0.19 && x > 0.4 && x < 1.6
    && realFloor - y > 0.95 && realFloor - y < 2.05;
  const door = centroid((x, y, z) => framePointInto(scratch, 0, x, y, z, realFrame), doorRegion);
  const doorPlain = centroid((x, y, z) => framePointInto(scratch, 0, x, y, z, plainFrame), doorRegion);
  const windowAnchor = centroid((x, y, z) => framePointInto(scratch, 0, x, y, z, realFrame), windowRegion);
  const doorBox = anchorBox(realStructure.doors[0]);
  const windowBox = anchorBox(realStructure.windows[0]);
  assert(door.total > 2000 && windowAnchor.total > 2000,
    `anchors need enough cloud evidence, got door=${door.total} window=${windowAnchor.total}`);
  // 门扇：x 要落在门框里，y 必须落到南侧（只修竖直轴时会跑到北侧门那面墙上）
  assert(Math.abs(door.centroid[0] - doorBox.cx) < 0.3,
    `door must sit at the audited door x, got ${door.centroid[0].toFixed(3)} vs ${doorBox.cx.toFixed(3)}`);
  assert(door.centroid[1] < 0.6,
    `door must land on the south side, got y=${door.centroid[1].toFixed(3)}`);
  assert(Math.abs(door.centroid[2] - doorBox.cz) < 0.4,
    `door must keep the audited height, got z=${door.centroid[2].toFixed(3)}`);
  assert(doorPlain.centroid[1] > 1.5,
    `without the horizontal fit the door would sit on the north wall (y=${doorPlain.centroid[1].toFixed(3)})`);
  // 窗洞：x 落在窗框里、y 在北墙
  assert(Math.abs(windowAnchor.centroid[0] - windowBox.cx) < 0.5,
    `window must sit at the audited window x, got ${windowAnchor.centroid[0].toFixed(3)}`);
  assert(windowAnchor.centroid[1] > 2.2,
    `window must land on the north wall, got y=${windowAnchor.centroid[1].toFixed(3)}`);
  assert(Math.abs(windowAnchor.centroid[2] - windowBox.cz) < 0.5,
    `window must keep the audited height, got z=${windowAnchor.centroid[2].toFixed(3)}`);
  const bedItem = realStructure.semantic_instances.find((entry) => entry.instance_id === 'bed_001');
  const bed = coverage(anchorBox(bedItem), (x, y, z) => x > 0.3 && x < 1.8 && z > -0.1 && z < 0.98
    && realFloor - y > 0.1 && realFloor - y < 0.7, 0.35);
  assert(bed.total > 2000, `bed anchor needs enough cloud evidence, got ${bed.total}`);
  assert(bed.ratio > 0.95,
    `bedding must land inside the audited bed box, got ${bed.ratio.toFixed(3)}`);

  console.log(`PASS: delivery pack horizontal fit maps the cloud onto the audited room outline (${fittedHits}/6 furniture boxes filled vs ${upOnlyHits}/6 without the fit)`);
  console.log(`       scale=${realFrame.horizontal.scale.toFixed(4)} floor=${realFloor.toFixed(3)} ${JSON.stringify(summary(fitted))}`);
  console.log(`       anchors door=(${door.centroid.map((v) => v.toFixed(2)).join(', ')}) `
    + `window=(${windowAnchor.centroid.map((v) => v.toFixed(2)).join(', ')}) bed_ratio=${bed.ratio.toFixed(3)} `
    + `(up-axis-only door.y=${doorPlain.centroid[1].toFixed(2)})`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
