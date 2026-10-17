const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');

async function loadPlaywright() {
  try {
    return require('playwright');
  } catch {
    return require(path.join(
      os.homedir(),
      '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright',
    ));
  }
}

(async () => {
  let referenceBody = null;
  let uploadCount = 0;
  const scan = {
    id: 31,
    project_id: 1,
    status: 'awaiting_video',
    progress: 0,
    message: '',
    capture_type: 'video',
    reference_measurements: [],
    created_at: new Date().toISOString(),
  };

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const json = (body, status = 200) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(body));
    };
    if (url.pathname === '/api/projects/1') {
      return json({ id: 1, name: '参考项测试', address: '' });
    }
    if (url.pathname === '/api/projects/1/scans') {
      return json([scan]);
    }
    if (req.method === 'PUT' && url.pathname === '/api/scans/31/references') {
      let body = '';
      for await (const chunk of req) body += chunk;
      referenceBody = JSON.parse(body);
      if (referenceBody.measurements?.[0]?.name === '未知项') {
        return json({
          error: {
            code: 'validation_error',
            message: '请求参数校验失败',
            details: [{ message: '无法识别参考项“未知项”，请填写“床长度”' }],
          },
        }, 422);
      }
      return json({ ...scan, reference_measurements: referenceBody.measurements });
    }
    if (req.method === 'POST' && url.pathname === '/api/scans/31/upload') {
      uploadCount += 1;
      for await (const _chunk of req) { /* 丢弃合成的 multipart 请求体 */ }
      return json({ ...scan, status: 'uploaded' });
    }
    if (url.pathname.startsWith('/api/')) {
      return json({ detail: '合成测试无此资源' }, 404);
    }

    const staticRoot = path.resolve(__dirname, '../app/static');
    const relative = url.pathname === '/'
      ? 'console/index.html'
      : url.pathname.replace(/^\/(console|preview)-static\//, '$1/');
    const file = path.resolve(staticRoot, relative);
    if (!file.startsWith(staticRoot + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404);
      res.end();
      return;
    }
    const type = {
      '.js': 'text/javascript',
      '.mjs': 'text/javascript',
      '.html': 'text/html',
      '.css': 'text/css',
    }[path.extname(file)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type });
    fs.createReadStream(file).pipe(res);
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const playwright = await loadPlaywright();
  const browser = await playwright.chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {}),
  });
  try {
    const base = `http://127.0.0.1:${server.address().port}/`;
    const page = await browser.newPage({
      viewport: { width: 1024, height: 900 },
      reducedMotion: 'reduce',
    });
    page.setDefaultTimeout(10000);
    page.on('pageerror', (error) => console.error('PAGE ERROR:', error));
    await page.addInitScript(() => {
      localStorage.setItem('anjing.console.session', JSON.stringify({
        token: 'synthetic-test',
        user: { name: '测试管理员', role: 'admin' },
      }));
    });
    await page.goto(`${base}#/projects/1`);
    await page.locator('.scan-row[data-id="31"] [data-action="upload"]').click();

    const dialog = page.getByRole('dialog');
    await dialog.waitFor();
    await dialog.locator('summary').click();
    const rows = dialog.locator('[data-reference-row]');
    assert.equal(await rows.count(), 1, '上传弹窗应默认提供一个参考项');
    assert.equal(
      await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
      true,
      '1024px 视口下参考项不应横向溢出',
    );

    await rows.first().locator('[data-reference-name]').fill('床长度');
    await dialog.locator('#upload-file').setInputFiles({
      name: 'room.mp4',
      mimeType: 'video/mp4',
      buffer: Buffer.from('synthetic video'),
    });
    await dialog.getByRole('button', { name: '开始上传' }).click();
    const partialToast = page.locator('.toast.error');
    await partialToast.waitFor();
    assert.match(await partialToast.textContent(), /名称和尺寸/);
    assert.equal(referenceBody, null, '未填写完整时不应提交参考项');
    assert.equal(uploadCount, 0, '未填写完整时不应开始上传');

    await rows.first().locator('[data-reference-meters]').fill('2.05');
    await rows.first().locator('[data-reference-name]').fill('未知项');
    await dialog.getByRole('button', { name: '开始上传' }).click();
    const validationToast = page.locator('.toast.error').filter({ hasText: '无法识别参考项' });
    await validationToast.waitFor();
    assert.equal(uploadCount, 0, '后端拒绝参考项时不应开始上传');

    await rows.first().locator('[data-reference-remove]').click();
    assert.equal(await rows.count(), 0, '参考项应可全部删除');
    assert.equal(await dialog.locator('#reference-count').textContent(), '0 / 3');
    assert.equal(await dialog.locator('#reference-add').isDisabled(), false);
    await dialog.locator('#reference-add').click();
    assert.equal(await rows.count(), 1, '删除全部后应可重新添加');
    await rows.first().locator('[data-reference-name]').fill('床长度');
    await rows.first().locator('[data-reference-meters]').fill('2.05');
    await dialog.locator('#reference-add').click();
    await dialog.locator('#reference-add').click();
    assert.equal(await rows.count(), 3, '最多允许三个参考项');
    assert.equal(await dialog.locator('#reference-add').isDisabled(), true);
    await page.setViewportSize({ width: 1440, height: 1000 });
    assert.equal(
      await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
      true,
      '1440px 视口下参考项不应横向溢出',
    );
    if (process.env.REFERENCE_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.REFERENCE_SCREENSHOT_DIR, { recursive: true });
      await dialog.screenshot({
        path: path.join(process.env.REFERENCE_SCREENSHOT_DIR, 'reference-rows-1440.png'),
      });
    }

    await rows.nth(2).locator('[data-reference-remove]').click();
    assert.equal(await rows.count(), 2, '参考项应可删除');
    assert.equal(await dialog.locator('#reference-add').isDisabled(), false);
    await rows.nth(1).locator('[data-reference-remove]').click();
    assert.equal(await rows.count(), 1, '参考项应可删至只剩一项');

    await dialog.getByRole('button', { name: '开始上传' }).click();
    await dialog.getByRole('button', { name: '开始重新重建与分析' }).waitFor();
    assert.deepEqual(referenceBody, {
      measurements: [{ name: '床长度', meters: 2.05 }],
    });
    assert.equal(uploadCount, 1);
    console.log('PASS: upload dialog accepts named reference rows and guards partial input');
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
