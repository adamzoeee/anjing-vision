const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const os = require('node:os');

// A different candidate must never reuse another candidate's geometry or score.
const layout = (x) => ({ walls: [], doors: [], windows: [], objects: [
  { id: 'desk_001', label: 'desk', center: [x, 1, .4], size: [1, .5, .8] },
] });
const proposals = [1, 2].map((n) => ({
  title: n === 1 ? '<img src=x onerror="window.injected=1">' : '移动桌子',
  intent: n === 1 ? '<script>window.injected=1</script>' : '保留家具，扩大通行空间',
  edits: [{ op: 'move', target: 'desk_001', dx: n * .2, dy: 0 }],
  risk_codes: ['M01'], layout_before: layout(1), layout_after: layout(1 + n * .2),
  comparison: { before_score: 40, after_score: 50 + n, score_delta: 10 + n },
  metric_changes: [{ metric_code: 'M01', name: '通行净宽', before: .5, after: .7 + n * .1, unit: 'm' }],
  risk_changes: [{ metric_code: 'M01', name: '通行风险', before: 'high', after: 'medium' }],
  evidence: { facts: { source: 'structured_scan', score: 40, risks: [{ metric_code: 'M01', risk_name: '通行风险', measured_value: .5, unit: 'm' }] },
    design_intent: { source: 'model', text: '扩大通行空间' },
    verification: { source: 'build_scheme', improvements: ['M01'] } },
  limitations: ['空间模拟，须现场复核'], disclaimer: '沙盒模拟 · 非实测', requires_construction: false,
}));
const fingerprints = { measurements_sha256: 'abc', structure_sha256: 'def' };
const rotationProposals = [1, 2, 3].map((quarter_turns) => ({
  ...proposals[1], title: `旋转方案 ${quarter_turns}`,
  edits: [{ op: 'rotate', target: 'desk_001', quarter_turns }],
  layout_after: { ...layout(1), objects: layout(1).objects.map((item) => ({
    ...item, rotation_z_deg: 90 * quarter_turns,
    size: quarter_turns % 2 ? [.5, 1, .8] : item.size,
  })) },
}));

(async () => {
  const { candidateViewModel, proposalSavePayload, candidateLayout, describeProposalEdit, proposalNarrativeHTML } = await import('../app/static/console/js/views/assistant_model.mjs');
  for (const [quarter_turns, degrees] of [[1, 90], [2, 180], [3, 270]]) {
    assert.equal(describeProposalEdit({ op: 'rotate', target: 'desk_001', quarter_turns }), `旋转 desk_001 ${degrees}°`);
  }
  assert.equal(describeProposalEdit({ op: 'rotate', target: 'desk_001' }), '旋转 desk_001 90°', 'legacy omitted quarter_turns defaults to one turn like the backend');
  const a = candidateViewModel(proposals[0]);
  const b = candidateViewModel(proposals[1]);
  assert.equal(a.comparison.after_score, 51);
  assert.equal(b.comparison.after_score, 52);
  assert.equal(candidateLayout(a, 'after').layout.objects[0].center[0], 1.2);
  assert.equal(candidateLayout(b, 'after').layout.objects[0].center[0], 1.4);
  assert.equal(candidateLayout(b, 'before').layout.objects[0].center[0], 1);
  assert.deepEqual(candidateLayout(b, 'before').marks, { changed: ['desk_001'], removed: [] });
  assert.deepEqual(proposalSavePayload(proposals[1], fingerprints), {
    name: '移动桌子', source: 'ai', edits: [{ op: 'move', target: 'desk_001', dx: .4, dy: 0 }],
    based_on: fingerprints, proposal_meta: { title: '移动桌子', intent: '保留家具，扩大通行空间', risk_codes: ['M01'], limitations: ['空间模拟，须现场复核'] },
  });
  assert.throws(() => proposalSavePayload(proposals[1], null), /指纹/);
  const savedNarrative = proposalNarrativeHTML({ intent: '保留家具', evidence: {
    facts: { score: 40, risks: [] }, verification: { comparison: { before_score: 40, after_score: 52 } },
  } }, (value) => String(value), 'saved');
  assert(savedNarrative.includes('保存时验算'));
  assert(!savedNarrative.includes('生成时验算'));

  // Real DOM and production router/view modules; only HTTP/model responses are synthetic.
  let playwright;
  try { playwright = require('playwright'); } catch {
    playwright = require(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
  }
  const staticRoot = path.resolve(__dirname, '../app/static');
  let mode = 'ready'; let saved; let pending; let generationCalls = 0; let saveConflict = false;
  let scanStatus = 'done'; let captureType = 'video'; let staleDetail = false;
  let lastGoal; let newScanCreated = 0; let uploadedVideo = ''; let starts = 0; let failStart = false;
  let newScanStatus = 'awaiting_video'; let failUpload = false;
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const json = (body, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
    if (url.pathname.startsWith('/api/')) {
      if (url.pathname === '/api/projects/1/scans' && req.method === 'POST') {
        let body = ''; for await (const chunk of req) body += chunk;
        assert.equal(JSON.parse(body).defer_processing, true);
        newScanCreated++; return json({ id: 9, project_id: 1, status: 'awaiting_video', capture_type: 'video' });
      }
      if (url.pathname === '/api/projects/1/scans') return json([
        { id: 9, project_id: 1, status: newScanStatus, capture_type: 'video', progress: 0 },
      ]);
      if (url.pathname === '/api/scans/9/upload') {
        assert.equal(url.searchParams.get('defer_processing'), 'true');
        let body = ''; for await (const chunk of req) body += chunk;
        uploadedVideo = body;
        if (failUpload) return json({ error: { message: '测试上传中断，请重试' } }, 503);
        newScanStatus = 'uploaded';
        return json({ ok: true, media: 'media/9/new.mp4' });
      }
      if (url.pathname === '/api/scans/9/start') {
        if (failStart) return json({ error: { message: '任务队列暂不可用，视频已保留，请稍后重试' } }, 503);
        starts++; newScanStatus = 'extracting'; return json({ ok: true, scan_id: 9 });
      }
      if (url.pathname === '/api/scans/9') return json({ id: 9, project_id: 1, status: newScanStatus, capture_type: 'video', progress: 0 });
      if (url.pathname.endsWith('/proposals')) {
        let body = ''; for await (const chunk of req) body += chunk;
        lastGoal = JSON.parse(body).goal;
        generationCalls++;
        if (mode === 'pending') { pending = res; return; }
        if (mode === 'error') return json({ error: { message: 'AI 服务暂时不可用，请重试' } }, 503);
        if (mode === 'insufficient') return json({ error: { message: '当前资料不足以生成多个可信方案，请补充测量后重试。' } }, 422);
        return json({ status: 'ready', scan_id: 7, input_fingerprints: fingerprints, proposals: mode === 'rotations' ? rotationProposals : proposals });
      }
      if (url.pathname.endsWith('/schemes') && req.method === 'POST') {
        let body = ''; for await (const chunk of req) body += chunk;
        if (saveConflict) return json({ detail: '扫描输入已更新，请重新生成方案后保存' }, 409);
        saved = JSON.parse(body); return json({ id: 8, ...saved });
      }
      if (url.pathname.endsWith('/schemes/8')) {
        const proposal = mode === 'rotations' ? rotationProposals.find((item) => item.title === saved.name) : proposals[1];
        return json({ ...proposal, stale: staleDetail,
          comparison: staleDetail ? { before_score: 60, after_score: 63, score_delta: 3 } : proposal.comparison,
          plan: { id: 8, name: saved.name, edits: saved.edits },
          proposal_meta: { ...saved.proposal_meta, evidence: { ...proposal.evidence,
            verification: { ...proposal.evidence.verification, comparison: proposal.comparison } } } });
      }
      if (url.pathname.endsWith('/schemes')) return json({ schemes: [] });
      if (url.pathname === '/api/scans/7') return json({ id: 7, project_id: 1, status: scanStatus, capture_type: captureType });
      if (url.pathname === '/api/projects/1') return json({ id: 1, name: '测试房间' });
      if (url.pathname === '/api/reports/scans/7') return json({ measures: { measurements: { room: { length_m: 4, width_m: 4, height_m: 2.7 } }, risk_assessment: { overall: {}, metrics: [], risks: [] } } });
      if (url.pathname.endsWith('/structure.json')) return json({ walls: [], doors: [], windows: [], semantic_instances: [] });
      if (url.pathname.endsWith('/layout.json')) return json(layout(1));
      if (url.pathname.endsWith('/suggestions')) return json({ suggestions: [] });
      return json({ detail: '合成测试无此资源' }, 404);
    }
    const relative = url.pathname === '/console' ? 'console/index.html' : url.pathname.replace(/^\/(console|preview)-static\//, '$1/');
    const file = path.resolve(staticRoot, relative);
    if (!file.startsWith(staticRoot + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    const type = { '.js': 'text/javascript', '.mjs': 'text/javascript', '.html': 'text/html', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type }); fs.createReadStream(file).pipe(res);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await playwright.chromium.launch({ headless: true,
      ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
        ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(10000);
    page.setDefaultNavigationTimeout(10000);
    await page.addInitScript(() => localStorage.setItem('anjing.console.session', JSON.stringify({ token: 'synthetic-test', user: { name: '测试用户', role: 'admin' } })));
    const base = `http://127.0.0.1:${server.address().port}/console`;
    await page.goto(`${base}#/scans/7`);
    await page.locator('#btn-room-assistant').waitFor();
    assert.equal(await page.getByText('AI 改造模拟', { exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '运行沙盒模拟' }).count(), 0);
    await page.locator('#btn-room-assistant').click();
    await page.waitForURL(/#\/scans\/7\/assistant$/);
    await page.locator('#assistant-goal').fill('保留床，扩大通行空间');
    await page.getByRole('button', { name: '生成改造方案', exact: true }).click();
    const cards = page.locator('.assistant-candidate');
    await cards.nth(1).waitFor();
    assert.equal(await cards.count(), 2);
    assert.equal(lastGoal, '保留床，扩大通行空间');
    assert.equal(await cards.nth(0).locator('img,script').count(), 0);
    assert((await cards.nth(0).textContent()).includes('<img src=x'));
    assert.equal(await page.evaluate(() => window.injected), undefined);
    const canvasImage = async () => {
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      return page.locator('#assistant-stage canvas').screenshot();
    };
    const firstCandidateImage = await canvasImage();
    await cards.nth(1).focus(); await page.keyboard.press('Enter');
    assert.equal(await cards.nth(1).getAttribute('aria-pressed'), 'true');
    assert((await cards.nth(1).textContent()).includes('当前预览'));
    assert(!(await cards.nth(0).textContent()).includes('当前预览'));
    assert((await page.locator('#assistant-evidence').textContent()).includes('52'));
    assert((await page.locator('#assistant-evidence .assistant-narrative').textContent()).includes('生成时验算'));
    assert(!(await page.locator('#assistant-evidence .assistant-narrative').textContent()).includes('保存时验算'));
    assert.equal(await page.locator('#assistant-stage canvas').count(), 1);
    assert.notDeepEqual(await canvasImage(), firstCandidateImage, 'switching candidates must change rendered geometry');
    for (const width of [375, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `no page overflow at ${width}px`);
      assert.equal(await page.getByRole('button', { name: '保存此方案', exact: true }).isVisible(), true);
    }
    if (process.env.ASSISTANT_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.ASSISTANT_SCREENSHOT_DIR, { recursive: true });
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.screenshot({ path: path.join(process.env.ASSISTANT_SCREENSHOT_DIR, 'assistant-desktop.png'), fullPage: true });
      await page.setViewportSize({ width: 375, height: 1000 });
      await page.screenshot({ path: path.join(process.env.ASSISTANT_SCREENSHOT_DIR, 'assistant-narrow.png'), fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
    const afterImage = await canvasImage();
    await page.getByRole('button', { name: '改造前', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: '改造前', exact: true }).getAttribute('aria-pressed'), 'true');
    assert.notDeepEqual(await canvasImage(), afterImage, 'before/after must render different layouts');
    await page.getByRole('button', { name: '改造后', exact: true }).click();
    await page.getByRole('button', { name: '保存此方案', exact: true }).click();
    await page.waitForURL(/#\/scans\/7\/schemes\/8$/);
    await page.getByText('保留家具，扩大通行空间', { exact: true }).waitFor();
    assert.deepEqual(saved.based_on, fingerprints); assert.equal(saved.source, 'ai');
    assert.equal(saved.proposal_meta.title, '移动桌子');
    staleDetail = true;
    await page.reload();
    await page.getByText('基于旧数据', { exact: true }).waitFor();
    assert((await page.locator('.sh-score').textContent()).includes('当前重算'));
    assert((await page.locator('.sh-score').textContent()).includes('63'));
    assert((await page.locator('.assistant-narrative').textContent()).includes('保存时验算'));
    assert((await page.locator('.assistant-narrative').textContent()).includes('52'));
    staleDetail = false;
    mode = 'rotations';
    for (const [index, degrees] of [[0, 90], [1, 180], [2, 270]]) {
      await page.goto(`${base}#/scans/7/assistant`);
      await page.getByRole('button', { name: '生成改造方案', exact: true }).click();
      await cards.nth(2).waitFor();
      await cards.nth(index).click();
      assert((await page.locator('#assistant-evidence ol li').textContent()).includes(`${degrees}°`), `candidate must describe ${degrees}°`);
      await page.getByRole('button', { name: '保存此方案', exact: true }).click();
      await page.waitForURL(/#\/scans\/7\/schemes\/8$/);
      await page.locator('.edit-list li').waitFor();
      assert((await page.locator('.edit-list li').textContent()).includes(`${degrees}°`), `saved scheme must describe ${degrees}°`);
    }
    mode = 'ready';
    await page.goto(`${base}#/scans/7/assistant`);
    await page.getByRole('button', { name: '生成改造方案', exact: true }).click();
    await cards.nth(1).waitFor();
    saveConflict = true;
    await page.getByRole('button', { name: '保存此方案', exact: true }).click();
    await page.getByText('扫描输入已更新，请重新生成方案后保存', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '保存此方案', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: '生成改造方案', exact: true }).isEnabled(), true);
    saveConflict = false;
    await page.goto(`${base}#/scans/7/assistant`);
    mode = 'error';
    await page.getByRole('button', { name: '生成改造方案', exact: true }).click();
    await page.getByText('AI 服务暂时不可用，请重试', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '生成改造方案', exact: true }).isEnabled(), true);
    mode = 'insufficient';
    await page.getByRole('button', { name: '生成改造方案', exact: true }).click();
    await page.getByText('当前资料不足以生成多个可信方案，请补充测量后重试。', { exact: true }).waitFor();
    assert.equal(await page.locator('#assistant-workspace').isVisible(), false);
    mode = 'pending';
    let started = page.waitForRequest((req) => req.url().endsWith('/proposals'));
    await page.getByRole('button', { name: '生成改造方案', exact: true }).click();
    await started;
    let aborted = page.waitForEvent('requestfailed', { predicate: (req) => req.url().endsWith('/proposals') });
    await page.getByRole('button', { name: '取消请求', exact: true }).click();
    assert((await aborted).failure().errorText.includes('ERR_ABORTED'));
    await page.getByText('已取消本次请求，可重新生成。', { exact: true }).waitFor();
    pending?.end(); pending = null;
    await page.setViewportSize({ width: 375, height: 800 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    mode = 'pending';
    started = page.waitForRequest((req) => req.url().endsWith('/proposals'));
    await page.getByRole('button', { name: '生成改造方案', exact: true }).click();
    await started;
    aborted = page.waitForEvent('requestfailed', { predicate: (req) => req.url().endsWith('/proposals') });
    await page.getByRole('link', { name: '返回房间', exact: true }).click();
    assert((await aborted).failure().errorText.includes('ERR_ABORTED'));
    await page.waitForURL(/#\/scans\/7$/);
    pending?.end(JSON.stringify({ status: 'ready', proposals, input_fingerprints: fingerprints }));
    await page.locator('#btn-room-assistant').waitFor();
    assert.equal(generationCalls, 9);
    assert.equal(await page.locator('.assistant-candidate').count(), 0);
    scanStatus = 'understanding';
    await page.goto(`${base}#/scans/7/assistant`);
    await page.getByText('请等待房间扫描完成后生成方案。', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '生成改造方案', exact: true }).isDisabled(), true);
    // Members can inspect simulated choices, but cannot persist a scheme.
    scanStatus = 'done'; captureType = 'video'; mode = 'ready';
    const memberPage = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await memberPage.addInitScript(() => localStorage.setItem('anjing.console.session', JSON.stringify({
      token: 'synthetic-member', user: { name: '只读成员', role: 'member' },
    })));
    await memberPage.goto(`${base}#/scans/7/assistant`);
    await memberPage.getByRole('button', { name: '生成改造方案', exact: true }).click();
    await memberPage.locator('.assistant-candidate').nth(1).waitFor();
    assert.equal(await memberPage.locator('#assistant-save').isDisabled(), true);
    await memberPage.getByRole('button', { name: '不满意，重新处理', exact: true }).click();
    assert.equal(await memberPage.getByRole('button', { name: '重新上传新视频', exact: true }).isDisabled(), true);
    await memberPage.close();
    // Reupload must require a fresh file and create a separate scan, never reuse scan 7's media.
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`${base}#/scans/7/assistant`);
    await page.reload();
    await page.getByRole('button', { name: '生成改造方案', exact: true }).click();
    await page.locator('.assistant-candidate').nth(1).waitFor();
    await page.getByRole('button', { name: '不满意，重新处理', exact: true }).click();
    const callsBeforeRetry = generationCalls;
    await page.getByRole('button', { name: '只重新生成方案', exact: true }).click();
    await page.locator('.assistant-candidate').nth(1).waitFor();
    assert.equal(generationCalls, callsBeforeRetry + 1);
    assert.equal(newScanCreated, 0);
    await page.getByRole('button', { name: '不满意，重新处理', exact: true }).click();
    if (process.env.ASSISTANT_SCREENSHOT_DIR) {
      await page.setViewportSize({ width: 1440, height: 1100 });
      await page.locator('#assistant-unsatisfied').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(process.env.ASSISTANT_SCREENSHOT_DIR, 'assistant-retry-options.png') });
    }
    await page.getByRole('button', { name: '重新上传新视频', exact: true }).click();
    await page.getByRole('button', { name: '开始上传', exact: true }).click();
    assert.equal(newScanCreated, 0, 'no new scan until a new file is chosen');
    await page.locator('#upload-file').setInputFiles({ name: 'new.mp4', mimeType: 'video/mp4', buffer: Buffer.from('fresh-video-content') });
    failUpload = true;
    await page.getByRole('button', { name: '开始上传', exact: true }).click();
    await page.getByText('测试上传中断，请重试', { exact: true }).waitFor();
    assert.equal(newScanCreated, 1);
    await page.getByRole('button', { name: '取消', exact: true }).click();
    await page.goto(`${base}#/projects/1`);
    await page.getByRole('button', { name: '上传视频', exact: true }).click();
    failUpload = false;
    await page.locator('#upload-file').setInputFiles({ name: 'new.mp4', mimeType: 'video/mp4', buffer: Buffer.from('fresh-video-content') });
    await page.getByRole('button', { name: '开始上传', exact: true }).click();
    await page.getByRole('button', { name: '开始重新重建与分析', exact: true }).waitFor();
    assert.equal(newScanCreated, 1);
    assert(uploadedVideo.includes('new.mp4') && uploadedVideo.includes('fresh-video-content'));
    assert.equal(starts, 0, 'upload must not start reconstruction implicitly');
    if (process.env.ASSISTANT_SCREENSHOT_DIR) {
      await page.screenshot({ path: path.join(process.env.ASSISTANT_SCREENSHOT_DIR, 'assistant-new-video-uploaded.png') });
    }
    failStart = true;
    await page.getByRole('button', { name: '开始重新重建与分析', exact: true }).click();
    await page.getByText('任务队列暂不可用，视频已保留，请稍后重试', { exact: true }).waitFor();
    assert.equal(starts, 0);
    assert.equal(newScanCreated, 1, 'queue failure must reuse the uploaded new video, not create another scan');
    failStart = false;
    // Closing the dialog must not lose the uploaded file or the start action.
    await page.getByRole('button', { name: '取消', exact: true }).click();
    await page.goto(`${base}#/scans/9`);
    await page.getByRole('button', { name: '开始重新重建与分析', exact: true }).waitFor();
    await page.getByRole('button', { name: '开始重新重建与分析', exact: true }).click();
    await page.waitForURL(/#\/scans\/9$/);
    await page.getByText('正在处理 · 视频抽帧', { exact: true }).waitFor();
    assert.equal(starts, 1);
    await page.goto(`${base}#/scans/7/assistant`);
    scanStatus = 'done'; captureType = 'house';
    await page.reload();
    await page.getByText('小助手仅支持单房间扫描，整屋扫描暂不可用。', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '生成改造方案', exact: true }).isDisabled(), true);
    console.log('PASS: assistant independent candidates, escaped content, room navigation, shared viewer, save/readback, errors, abort and narrow layout');
  } finally {
    pending?.end();
    await browser?.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
