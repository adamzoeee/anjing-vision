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
  let projects = [{
    id: 41,
    name: '张奶奶家',
    address: '幸福路 8 号',
  }];
  let patchedName = '';
  let deleted = false;

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const json = (body, status = 200) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(body));
    };
    if (url.pathname === '/api/projects' && req.method === 'GET') {
      return json(projects);
    }
    if (url.pathname === '/api/projects/41' && req.method === 'PATCH') {
      let body = '';
      for await (const chunk of req) body += chunk;
      patchedName = JSON.parse(body).name;
      projects = projects.map((project) => (
        project.id === 41 ? { ...project, name: patchedName } : project
      ));
      return json(projects[0]);
    }
    if (url.pathname === '/api/projects/41' && req.method === 'DELETE') {
      deleted = true;
      projects = [];
      res.writeHead(204);
      res.end();
      return;
    }
    if (url.pathname === '/api/projects/41/scans') {
      return json([]);
    }
    if (url.pathname === '/api/projects/42/scans') {
      return json([]);
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
    const base = `http://127.0.0.1:${server.address().port}/#/projects`;
    const admin = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    admin.setDefaultTimeout(10000);
    await admin.addInitScript(() => {
      localStorage.setItem('anjing.console.session', JSON.stringify({
        token: 'synthetic-test',
        user: { name: '测试管理员', role: 'admin' },
      }));
    });
    await admin.goto(base);
    const card = admin.locator('.project-card[data-id="41"]');
    await card.waitFor();
    assert.equal(await card.locator('[data-project-action="rename"]').count(), 1);
    assert.equal(await card.locator('[data-project-action="delete"]').count(), 1);

    await card.locator('[data-project-action="rename"]').click();
    const renameDialog = admin.getByRole('dialog');
    await renameDialog.waitFor();
    assert.equal(await renameDialog.locator('input').inputValue(), '张奶奶家');
    await renameDialog.locator('input').fill('张奶奶新居');
    await renameDialog.getByRole('button', { name: '保存名称' }).click();
    await admin.locator('.project-card[data-id="41"] h3', { hasText: '张奶奶新居' }).waitFor();
    assert.equal(patchedName, '张奶奶新居');

    await admin.locator('.project-card[data-id="41"] [data-project-action="delete"]').click();
    const deleteDialog = admin.getByRole('dialog');
    await deleteDialog.waitFor();
    assert.match(await deleteDialog.textContent(), /张奶奶新居/);
    assert.match(await deleteDialog.textContent(), /永久删除/);
    await deleteDialog.getByRole('button', { name: '确认删除' }).click();
    await admin.locator('.project-card[data-id="41"]').waitFor({ state: 'detached' });
    assert.equal(deleted, true);

    projects = [{ id: 42, name: '成员只读项目', address: '' }];
    const member = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await member.addInitScript(() => {
      localStorage.setItem('anjing.console.session', JSON.stringify({
        token: 'synthetic-test',
        user: { name: '测试成员', role: 'member' },
      }));
    });
    await member.goto(base);
    await member.locator('.project-card[data-id="42"]').waitFor();
    assert.equal(await member.locator('[data-project-action]').count(), 0);
    console.log('PASS: project rename and delete UI follows admin permissions');
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
