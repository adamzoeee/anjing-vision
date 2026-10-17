const assert = require('node:assert/strict');

(async () => {
  const {
    MAX_VIDEO_BYTES,
    MAX_VIDEO_LABEL,
    exceedsVideoLimit,
  } = await import('../app/static/console/js/upload_limits.mjs');

  assert.equal(MAX_VIDEO_BYTES, 1_610_612_736);
  assert.equal(MAX_VIDEO_LABEL, '1.5 GB');
  assert.equal(exceedsVideoLimit(MAX_VIDEO_BYTES), false);
  assert.equal(exceedsVideoLimit(MAX_VIDEO_BYTES + 1), true);
  console.log('PASS: video upload boundary accepts 1.5 GB and rejects one byte above');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
