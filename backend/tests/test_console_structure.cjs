const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

/* 扫描45/46 的 structure_calibrated.json 同时带两套家具字段：
 *  - objects           : 旧 SpatialLM 候选框（scan46 只有 5 件，位置是候选坐标，互相重叠）
 *  - semantic_instances: 审计后的 6 件实例（与 measurements.json / 2D 平面图逐项一致）
 * 大屏查看器与房间工作台都必须优先 semantic_instances，且位置/尺寸/旋转取自同一条实例记录。
 */
(async () => {
  const helper = await import('../app/static/console/js/viewer/structure.mjs');
  const { structureFurniture, normalizeStructure } = helper;
  assert.equal(typeof structureFurniture, 'function', 'structure.mjs must export structureFurniture');
  assert.equal(typeof normalizeStructure, 'function', 'structure.mjs must export normalizeStructure');

  const legacy = [
    { instance_id: 'bed_01', category: 'multifunctional_combination_bed', center: [0.607, 0.01, 0.362], size: [1.996, 1.514, 0.277] },
    { instance_id: 'nightstand_01', category: 'nightstand', center: [1.408, 0.756, 0.462], size: [1.199, 0.431, 0.291] },
  ];
  const semantic = [
    { instance_id: 'bed_001', label: 'bed', center: [0.5406, 2.034, 0.318], size: [2.05, 1.47, 0.6358], rotation_z_deg: 0 },
    { instance_id: 'bookshelf_001', label: 'bookshelf', center: [2.2377, 1.83, 0.91], size: [1.578125, 0.34375, 1.82], rotation_z_deg: 90 },
  ];
  const source = {
    walls: [{ center: [0, 0, 1.35], size: [2.894, 0.06, 2.71], rotation_z_deg: 0 }],
    doors: [{ center: [1.9267, -0.501, 1.05], size: [0.846, 0.1, 2.1] }],
    windows: [{ center: [0.65, 2.769, 1.575], size: [1.598, 0.1, 1.25] }],
    objects: legacy,
    semantic_instances: semantic,
  };

  // 有 semantic_instances 时必须完全忽略旧 objects
  const chosen = structureFurniture(source);
  assert.equal(chosen.length, 2, `expected semantic instances, got ${chosen.length}`);
  assert.deepEqual(chosen.map((item) => item.instance_id), ['bed_001', 'bookshelf_001']);
  assert(!chosen.some((item) => String(item.instance_id).endsWith('_01')),
    'legacy *_01 candidate boxes must not be mixed into the audited furniture list');

  const layout = normalizeStructure(source);
  assert.equal(layout.objects.length, 2, 'normalized layout must use the semantic furniture set');
  assert.equal(layout.walls.length, 1);
  assert.equal(layout.doors.length, 1);
  assert.equal(layout.windows.length, 1);
  const shelf = layout.objects.find((item) => item.instance_id === 'bookshelf_001');
  assert.deepEqual(shelf.center, [2.2377, 1.83, 0.91], 'position must come from the same instance record');
  assert.deepEqual(shelf.size, [1.578125, 0.34375, 1.82], 'size must come from the same instance record');
  assert.equal(shelf.rotation_z_deg, 90, 'rotation must come from the same instance record');
  assert.equal(shelf.category, 'bookshelf', 'label must supply the display category');
  const bed = layout.objects.find((item) => item.instance_id === 'bed_001');
  assert.equal(bed.category, 'bed', 'category must fall back to the instance id stem when no label exists');

  // 没有 semantic_instances 时回退到旧 objects（不能把回退路径删掉）
  const fallback = normalizeStructure({ walls: [], objects: legacy });
  assert.equal(fallback.objects.length, 2, 'legacy objects remain the fallback');
  assert.deepEqual(fallback.objects.map((item) => item.instance_id), ['bed_01', 'nightstand_01']);
  assert.equal(normalizeStructure({ objects: [] }).objects.length, 0);
  assert.equal(normalizeStructure(null).objects.length, 0);

  // 交付包真实数据：结构 6 件必须与 measurements.json 逐项对齐（位置/尺寸/旋转同源）
  const post = path.join(__dirname, '../data/work/46/postprocess');
  const structurePath = path.join(post, 'structure_calibrated.json');
  const measurementsPath = path.join(post, 'measurements.json');
  if (fs.existsSync(structurePath) && fs.existsSync(measurementsPath)) {
    const realStructure = JSON.parse(fs.readFileSync(structurePath, 'utf8'));
    const measurements = JSON.parse(fs.readFileSync(measurementsPath, 'utf8'));
    const real = normalizeStructure(realStructure);
    assert.equal(real.objects.length, measurements.objects.length,
      'structure furniture count must match measurements');
    assert.equal(real.objects.length, 6, 'scan46 has 6 audited furniture instances');
    assert.equal(realStructure.objects.length, 5, 'legacy field still lists 5 candidate boxes');
    assert(!real.objects.some((item) => item.instance_id === 'nightstand_01'),
      'nightstand_01 is a legacy candidate, not audited furniture');
    measurements.objects.forEach((measure) => {
      const match = real.objects.find((item) => item.instance_id === measure.id);
      assert(match, `missing structure instance for ${measure.id}`);
      assert.deepEqual(match.center, measure.center, `${measure.id} center must match measurements`);
      assert.deepEqual(match.size, [measure.length_m, measure.width_m, measure.height_m],
        `${measure.id} size must match measurements`);
      assert.equal(match.rotation_z_deg, measure.rotation_z_deg ?? 0,
        `${measure.id} rotation must match measurements`);
    });
  } else {
    console.log('SKIP: scan46 postprocess fixtures not present');
  }

  // 三个消费方必须共用同一套归一化，禁止再直接读旧字段
  const consoleJs = path.join(__dirname, '../app/static/console/js');
  const viewer = fs.readFileSync(path.join(consoleJs, 'viewer/pointcloud.js'), 'utf8');
  const room = fs.readFileSync(path.join(consoleJs, 'views/room.js'), 'utf8');
  const screen = fs.readFileSync(path.join(consoleJs, 'views/screen.js'), 'utf8');
  assert(viewer.includes("from './structure.mjs'"), 'pointcloud.js must import the shared structure helper');
  assert(room.includes("from '../viewer/structure.mjs'"), 'room workbench must use the shared structure helper');
  assert(screen.includes("from '../viewer/structure.mjs'"), 'big-screen mode must use the shared structure helper');
  [viewer, room, screen].forEach((src, index) => {
    const name = ['pointcloud.js', 'room.js', 'screen.js'][index];
    assert(!/structure\??\.objects/.test(src), `${name} must not read the legacy structure.objects field`);
    assert(!/source\.objects/.test(src), `${name} must not read the legacy source.objects field`);
  });

  console.log('PASS: console furniture comes from semantic_instances and matches the measured plan');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
