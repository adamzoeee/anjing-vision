const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const helper = await import('../app/static/console/js/viewer/frame.mjs');
  const { packUpAxis, resolveFrame, framePoint } = helper;

  // 扫描46交付包：front 视图 R[1]=(0,1,0) → 屏幕 up = -R[1] = -Y（来源帧 Y 向下）
  const packFront = { R: [[-1, 0, 0], [0, 1, 0], [0, 0, -1]] };
  const up = packUpAxis({ front: packFront });
  assert(up, 'expected an up axis');
  assert(Math.abs(up[0]) < 1e-9 && Math.abs(up[1] + 1) < 1e-9 && Math.abs(up[2]) < 1e-9,
    `up should be -Y, got ${up}`);

  const frame = resolveFrame({ front: packFront }, 1.473);
  assert(frame, 'y-vertical pack needs a correction');
  assert.equal(frame.kind, 'y-vertical');
  const close = (actual, expected) => assert.ok(
    actual.length === expected.length && actual.every((value, index) => Math.abs(value - expected[index]) < 1e-9),
    `${JSON.stringify(actual)} != ${JSON.stringify(expected)}`,
  );
  close(framePoint(0, 1.473, 2, frame), [0, 2, 0]);        // 地板 → 结构系 z=0
  close(framePoint(0, -0.94, 2, frame), [0, 2, 2.413]);    // 天花板方向 → +z
  close(framePoint(0.5, -1, 0.25, frame), [0.5, 0.25, 2.473]);
  const upDir = framePoint(0, -1, 0, frame);
  assert(upDir[2] > upDir[0] && upDir[2] > upDir[1], 'pack up must map to +z');

  // z-up 交付包（front 相机 up = +Z）不需要校正
  const zUpFront = { R: [[1, 0, 0], [0, 0, -1], [0, 1, 0]] };
  assert.equal(packUpAxis({ front: zUpFront })[2] > 0.99, true, 'expected +Z up');
  assert.equal(resolveFrame({ front: zUpFront }, 0), null);

  // y-up（up = +Y）也需要校正，但地板在最小 y 一侧
  const yUpFront = { R: [[1, 0, 0], [0, -1, 0], [0, 0, -1]] };
  const yUpFrame = resolveFrame({ front: yUpFront }, -1.2);
  assert(yUpFrame && yUpFrame.kind === 'y-vertical');
  close(framePoint(0, -1.2, 3, yUpFrame), [0, -3, 0]);
  close(framePoint(0, 1.2, 3, yUpFrame), [0, -3, 2.4]);

  // 缺元数据时不乱改
  assert.equal(resolveFrame(null, 0), null);
  assert.deepEqual(framePoint(1, 2, 3, null), [1, 2, 3]);

  // 大屏查看器必须把同一套校正接进点云与展示层
  const src = fs.readFileSync(path.join(__dirname, '../app/static/console/js/viewer/pointcloud.js'), 'utf8');
  assert(src.includes("from './frame.mjs'"), 'pointcloud.js must import the frame helper');
  assert(/addPointChunks\(\s*meta,\s*data,\s*frame\b/.test(src), 'point cloud must be frame-corrected');
  assert(/buildVisualMesh\(data,\s*await textureFor\(data\),\s*frame\)/.test(src),
    'visual meshes must share the same correction');
  assert(/sampleFloorAnchor\(/.test(src), 'floor anchor must come from the loaded cloud');

  console.log('PASS: console frame correction maps y-vertical scan46 packs into the z-up business frame');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
