const assert = require('node:assert/strict');

/* 房间与整屋视图模式回归：纯点云、纯框架、整屋叠加互不串扰。 */
(async () => {
  const { layerVisibility } = await import('../app/static/console/js/viewer/mode.mjs');
  assert.equal(typeof layerVisibility, 'function', 'mode.mjs must export layerVisibility');

  const allOn = { pcd: true, walls: true, doors: true, windows: true, objects: true, labels: true };

  // 真实场景：纯点云，框架/标签/网格全部不渲染
  const scene = layerVisibility('scene', allOn, true);
  assert.equal(scene.pcd, true, '真实场景必须显示点云');
  assert.equal(scene.boxes, false, '真实场景不得渲染结构框架');
  assert.equal(scene.labels, false, '真实场景不得渲染框架标签');
  assert.equal(scene.grid, false, '真实场景不显示结构网格');

  // 空间结构：点云隐藏，框架与标签按开关显示，网格启用
  const structure = layerVisibility('structure', allOn, true);
  assert.equal(structure.pcd, false, '空间结构隐藏点云');
  assert.equal(structure.boxes, true, '空间结构渲染结构框架');
  assert.equal(structure.labels, true, '标签开关打开时渲染标签');
  assert.equal(structure.grid, true, '空间结构启用网格');
  assert.equal(structure.walls && structure.doors && structure.windows && structure.objects, true);

  // 整屋叠加：点云与框架同时出现，且没有遮挡场景的背景网格。
  const overlay = layerVisibility('overlay', allOn, true);
  assert.equal(overlay.pcd, true);
  assert.equal(overlay.boxes, true);
  assert.equal(overlay.labels, true);
  assert.equal(overlay.grid, false);

  // 图层开关照常生效（结构视图的墙/门/窗/家具/标签过滤）
  const filtered = layerVisibility('structure', {
    pcd: true, walls: false, doors: true, windows: false, objects: true, labels: false,
  }, true);
  assert.equal(filtered.walls, false);
  assert.equal(filtered.doors, true);
  assert.equal(filtered.windows, false);
  assert.equal(filtered.objects, true);
  assert.equal(filtered.labels, false, '关掉标签后结构视图不再显示标签');

  // 结构数据还没就绪时：结构模式也不画框，更不能在真实场景“抢跑”
  const bare = layerVisibility('structure', allOn, false);
  assert.equal(bare.boxes, false);
  assert.equal(bare.labels, false);
  assert.equal(layerVisibility('scene', allOn, false).boxes, false);

  // 未知模式按真实场景处理（宁可少画，不要多画）
  assert.equal(layerVisibility('', allOn, true).boxes, false);
  assert.equal(layerVisibility(undefined, allOn, true).boxes, false);

  // 缺省图层视为开启；真实场景关掉点云开关仍然有效
  assert.equal(layerVisibility('structure', {}, true).walls, true);
  assert.equal(layerVisibility('scene', { pcd: false }, true).pcd, false);

  console.log('PASS: point cloud, framework and whole-house overlay modes');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
