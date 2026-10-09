const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

/* 交付包（window_wall_joined_20261008）除了竖直轴是 Y，水平面还相对业务系绕竖轴转了 180°，
 * 并整体小了约 10%。只做“竖直轴映射”会把窗帘/床头挂到门那面墙，且家具框与点云对不上。
 * 这里用“点云包络 ↔ 房间轮廓”做刚体拟合：两轴推算出的尺度必须一致，否则拒绝拉伸。
 */
(async () => {
  const helper = await import('../app/static/console/js/viewer/frame.mjs');
  const { resolveFrame, framePoint, fitHorizontalFrame, percentile, envelope } = helper;
  assert.equal(typeof fitHorizontalFrame, 'function', 'frame.mjs must export fitHorizontalFrame');
  assert.equal(typeof envelope, 'function', 'frame.mjs must export envelope');

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
  const step = Math.max(1, Math.floor(count / 40000));
  for (let i = 0; i < count; i += step) {
    const base = headerEnd + i * stride;
    xs.push(read(props[ix], base));
    ys.push(read(props[iy], base));
    zs.push(read(props[iz], base));
  }
  const realExtent = { x: envelope(xs), z: envelope(zs) };
  const realRoom = JSON.parse(fs.readFileSync(structurePath, 'utf8')).room.bounds_xy;
  const realFrame = resolveFrame(packViews, percentile(ys, 99.6), { pointExtent: realExtent, roomBounds: realRoom });
  assert(realFrame && realFrame.horizontal, 'real delivery pack must produce a horizontal fit');

  const measurements = JSON.parse(fs.readFileSync(measurementsPath, 'utf8'));
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
  const fitted = countInBoxes((x, y, z) => framePoint(x, y, z, realFrame));
  const plainFrame = resolveFrame(packViews, percentile(ys, 99.6));
  const upOnly = countInBoxes((x, y, z) => framePoint(x, y, z, plainFrame));
  const threshold = 80;
  const fittedHits = fitted.filter((box) => box.hits >= threshold).length;
  const upOnlyHits = upOnly.filter((box) => box.hits >= threshold).length;
  const summary = (list) => Object.fromEntries(list.map((box) => [box.id, box.hits]));
  assert(fittedHits >= 5,
    `fitted frame must place the audited furniture on cloud geometry (got ${fittedHits}/6: ${JSON.stringify(summary(fitted))})`);
  assert(fittedHits > upOnlyHits,
    `horizontal fit must beat the up-axis-only mapping (${fittedHits} vs ${upOnlyHits}: ${JSON.stringify(summary(upOnly))})`);

  console.log(`PASS: delivery pack horizontal fit maps the cloud onto the audited room outline (${fittedHits}/6 furniture boxes filled vs ${upOnlyHits}/6 without the fit)`);
  console.log(`       scale=${realFrame.horizontal.scale.toFixed(4)} ${JSON.stringify(summary(fitted))}`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
