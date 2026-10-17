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
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/api/')) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end('{"detail":"synthetic test"}');
      return;
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
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.locator('#login-form').waitFor();

    for (const width of [375, 1024, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const field of ['email', 'password']) {
        const label = page.locator(`label[for="login-${field}"]`);
        const hint = page.locator(`#login-${field}-demo`);
        await hint.waitFor();
        assert.match(await hint.textContent(), /demo@anjing-vision\.com/);
        const layout = await page.evaluate(({ field }) => {
          const labelEl = document.querySelector(`label[for="login-${field}"]`);
          const hintEl = document.querySelector(`#login-${field}-demo`);
          const labelBox = labelEl.getBoundingClientRect();
          const hintBox = hintEl.getBoundingClientRect();
          return {
            hintToRight: hintBox.left >= labelBox.right,
            sameLine: Math.abs(
              (hintBox.top + hintBox.height / 2) - (labelBox.top + labelBox.height / 2),
            ) < 8,
            noOverflow: document.documentElement.scrollWidth <= window.innerWidth,
          };
        }, { field });
        assert.equal(layout.hintToRight, true, `${field} hint at ${width}px must be right of its label`);
        assert.equal(layout.sameLine, true, `${field} hint at ${width}px must share the label line`);
        assert.equal(layout.noOverflow, true, `${width}px layout must not overflow horizontally`);
      }
    }

    assert.equal(await page.locator('#login-email').inputValue(), '');
    assert.equal(await page.locator('#login-password').inputValue(), '');
    console.log('PASS: demo credentials appear beside the email and password labels');
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
