const assert = require('node:assert/strict');

/* 方案可视化（改造前/后对照）纯数据回归：
 * layoutLayers 把方案接口的 layout_before/after 归一成 3D/2D 共用的图层；
 * diffLayout 标出被方案改动过、以及新增/移除的对象 id，供 3D 高亮与前后对照。 */
(async () => {
  const helper = await import('../app/static/console/js/viewer/scheme3d.mjs');
  const { layoutLayers, diffLayout, SCHEME3D_STYLE } = helper;
  ['layoutLayers', 'diffLayout'].forEach((name) =>
    assert.equal(typeof helper[name], 'function', `scheme3d.mjs must export ${name}`));
  assert(SCHEME3D_STYLE.wall && SCHEME3D_STYLE.door && SCHEME3D_STYLE.window && SCHEME3D_STYLE.object,
    'layer styles must exist for wall/door/window/object');

  const layout = {
    walls: [{ center: [1, 2, 1.35], size: [3, 0.12, 2.7], rotation_z_deg: 90, id: 'w0' }],
    doors: [{ center: [0.6, 0, 1.05], size: [0.85, 0.12, 2.1], id: 'door_01' }],
    windows: [{ center: [1.2, 0, 1.5], size: [1.4, 0.12, 1.6], id: 'win_01' }],
    objects: [
      { center: [1, 3, 0.3], size: [2, 1.2, 0.6], rotation_z_deg: 0, id: 'bed_001', label: 'bed' },
      { center: [1.2, 2, 0.4], size: [1, 0.5, 0.8], id: 'desk_001', label: 'desk', kind: 'object' },
      { center: [1.5, 1.5, 0.25], size: [0.5, 0.5, 0.5], id: 'box_1', kind: 'obstacle', assumed: true },
    ],
  };
  const layers = layoutLayers(layout);
  assert.equal(layers.walls.length, 1, 'walls must pass through');
  assert.equal(layers.doors.length, 1);
  assert.equal(layers.windows.length, 1);
  assert.equal(layers.objects.length, 3);
  const desk = layers.objects.find((item) => item.id === 'desk_001');
  assert.equal(desk.kind, 'object');
  assert.equal(desk.rotation_z_deg, 0, 'missing rotation must default to 0');
  const box = layers.objects.find((item) => item.id === 'box_1');
  assert.equal(box.kind, 'obstacle');
  assert.equal(box.assumed, true, 'assumed objects must stay flagged for the 假设放置 marker');

  // 脏数据不崩：缺 center/size 的条目直接被过滤
  const dirty = layoutLayers({ walls: [null, { size: [1, 1, 1] }, { center: [0, 0, 0] }] });
  assert.equal(dirty.walls.length, 0, 'invalid items must be dropped');
  assert.equal(layoutLayers(null).objects.length, 0, 'null layout must yield empty layers');

  // 前后差异：挪动/改尺寸/旋转算 changed；只在单侧出现算 added/removed
  const before = layout;
  const after = {
    ...layout,
    objects: [
      { center: [1, 3, 0.3], size: [2, 1.2, 0.6], rotation_z_deg: 0, id: 'bed_001', label: 'bed' },
      { center: [1.2, 1.4, 0.4], size: [1, 0.5, 0.8], id: 'desk_001', label: 'desk' },
      { center: [1.5, 1.5, 0.25], size: [0.5, 0.5, 0.5], id: 'box_1', kind: 'obstacle', assumed: true },
      { center: [2.5, 2.5, 0.3], size: [0.4, 0.4, 0.6], id: 'box_new', kind: 'obstacle', assumed: true },
    ],
  };
  const diff = diffLayout(before, after);
  assert.deepEqual(diff.changed, ['desk_001'], `desk_001 moved, others unchanged: ${JSON.stringify(diff)}`);
  assert.deepEqual(diff.added, ['box_new']);
  assert.deepEqual(diff.removed, []);

  const shrunk = diffLayout(before, {
    ...layout,
    objects: layout.objects.filter((item) => item.id !== 'box_1').map((item) =>
      (item.id === 'desk_001' ? { ...item, size: [1.2, 0.5, 0.8] } : item)),
  });
  assert.deepEqual(shrunk.changed, ['desk_001'], 'resize must count as changed');
  assert.deepEqual(shrunk.removed, ['box_1']);

  const rotated = diffLayout(before, {
    ...layout,
    objects: layout.objects.map((item) =>
      (item.id === 'bed_001' ? { ...item, rotation_z_deg: 90 } : item)),
  });
  assert.deepEqual(rotated.changed, ['bed_001'], 'rotation must count as changed');

  console.log('PASS: scheme layers normalize and the before/after diff marks moved, added and removed objects');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
