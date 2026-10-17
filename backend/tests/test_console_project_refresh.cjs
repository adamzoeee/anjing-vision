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
  const scans = [
    {
      id: 21,
      project_id: 1,
      status: 'extracting',
      progress: 18,
      message: '正在抽取视频帧',
      capture_type: 'video',
      created_at: new Date().toISOString(),
    },
    {
      id: 22,
      project_id: 1,
      status: 'done',
      progress: 100,
      message: '评估完成',
      capture_type: 'video',
      created_at: new Date().toISOString(),
    },
  ];

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const json = (body, status = 200) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(body));
    };
    if (url.pathname === '/api/projects/1') {
      return json({ id: 1, name: '增量刷新测试', address: '' });
    }
    if (url.pathname === '/api/projects/1/scans') {
      return json(scans);
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
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(10000);
    await page.addInitScript(() => {
      localStorage.setItem('anjing.console.session', JSON.stringify({
        token: 'synthetic-test',
        user: { name: '测试管理员', role: 'admin' },
      }));
    });
    const base = `http://127.0.0.1:${server.address().port}/`;
    await page.goto(`${base}#/projects/1`);
    const row = page.locator('.scan-row[data-id="21"]');
    await row.waitFor();
    await page.locator('.scan-row[data-id="22"]').waitFor();
    await page.evaluate(() => {
      window.__scanRow = document.querySelector('.scan-row[data-id="21"]');
      window.__projectHero = document.querySelector('.project-hero');
      window.__openButton = document.querySelector('.scan-row[data-id="22"] [data-action="open"]');
    });

    scans[0].status = 'reconstructing';
    scans[0].progress = 64;
    scans[0].message = 'SLAM3R 稠密重建中';
    await page.getByRole('button', { name: '刷新' }).click();
    await page.locator('.scan-row[data-id="21"] .scan-progress .num').filter({ hasText: '64%' }).waitFor();

    const preserved = await page.evaluate(() => ({
      row: window.__scanRow === document.querySelector('.scan-row[data-id="21"]'),
      hero: window.__projectHero === document.querySelector('.project-hero'),
      action: window.__openButton === document.querySelector('.scan-row[data-id="22"] [data-action="open"]'),
    }));
    assert.equal(preserved.row, true, 'refresh must keep the existing scan row node');
    assert.equal(preserved.hero, true, 'refresh must keep the existing page shell node');
    assert.equal(preserved.action, true, 'unchanged scan actions must keep their DOM nodes');
    assert.match(await row.textContent(), /SLAM3R 稠密重建中/);
    console.log('PASS: project refresh updates scan progress without replacing page DOM');
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
