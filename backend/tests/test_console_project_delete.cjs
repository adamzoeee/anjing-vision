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

function scan(id, status) {
  return {
    id,
    project_id: 1,
    status,
    progress: status === 'done' ? 100 : 0,
    message: '',
    capture_type: 'video',
    created_at: new Date().toISOString(),
  };
}

(async () => {
  const scans = [
    scan(31, 'awaiting_video'),
    scan(32, 'done'),
    scan(33, 'reconstructing'),
    scan(34, 'failed'),
  ];
  const deleted = [];
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const json = (body, status = 200) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(body));
    };
    if (url.pathname === '/api/projects/1') {
      return json({ id: 1, name: '删除入口测试', address: '' });
    }
    if (url.pathname === '/api/projects/1/scans') {
      return json(scans);
    }
    if (req.method === 'DELETE' && /^\/api\/scans\/(\d+)$/.test(url.pathname)) {
      const id = Number(url.pathname.split('/').pop());
      deleted.push(id);
      const index = scans.findIndex((item) => item.id === id);
      if (index >= 0) scans.splice(index, 1);
      res.writeHead(204);
      res.end();
      return;
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
    const admin = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    admin.setDefaultTimeout(10000);
    admin.on('pageerror', (error) => console.error('ADMIN PAGE ERROR:', error));
    await admin.addInitScript(() => {
      localStorage.setItem('anjing.console.session', JSON.stringify({
        token: 'synthetic-test',
        user: { name: '测试管理员', role: 'admin' },
      }));
    });
    await admin.goto(`${base}#/projects/1`);
    await admin.locator('.scan-row[data-id="31"]').waitFor();

    assert.equal(await admin.locator('[data-action="delete"]').count(), 2);
    assert.equal(await admin.locator('.scan-row[data-id="33"] [data-action="delete"]').count(), 0);
    assert.equal(await admin.locator('.scan-row[data-id="34"] [data-action="delete"]').count(), 0);

    await admin.locator('.scan-row[data-id="31"] [data-action="delete"]').click();
    const dialog = admin.getByRole('dialog');
    await dialog.waitFor();
    assert.match(await dialog.textContent(), /Scan #31/);
    await admin.waitForFunction(() => {
      const element = document.querySelector('[role="dialog"]');
      return Boolean(element?.contains(document.activeElement));
    });
    if (process.env.PROJECT_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.PROJECT_SCREENSHOT_DIR, { recursive: true });
      await admin.screenshot({
        path: path.join(process.env.PROJECT_SCREENSHOT_DIR, 'scan-delete-dialog.png'),
        fullPage: true,
      });
    }
    assert.equal(
      await dialog.evaluate((element) => element.contains(document.activeElement)),
      true,
      'opening the confirmation dialog must move focus inside it',
    );
    await dialog.getByRole('button', { name: '确认删除' }).click();
    await admin.locator('.scan-row[data-id="31"]').waitFor({ state: 'detached' });
    assert.deepEqual(deleted, [31]);

    const member = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    member.on('pageerror', (error) => console.error('MEMBER PAGE ERROR:', error));
    await member.addInitScript(() => {
      localStorage.setItem('anjing.console.session', JSON.stringify({
        token: 'synthetic-test',
        user: { name: '测试成员', role: 'member' },
      }));
    });
    await member.goto(`${base}#/projects/1`);
    try {
      await member.locator('.scan-row[data-id="32"]').waitFor();
    } catch (error) {
      console.error(await member.locator('body').innerText());
      throw error;
    }
    assert.equal(await member.locator('[data-action="delete"]').count(), 0);
    console.log('PASS: scan delete entry follows permissions and removable states');
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
