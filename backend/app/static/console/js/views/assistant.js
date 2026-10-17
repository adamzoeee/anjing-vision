import { api, isAdmin } from '../api.js?v=house-flow-1';
import { esc, fmtScore, riskLevel, formatMeasurement } from '../ui.js';
import { icon } from '../icons.js';
import { navigate } from '../app.js';
import { createSchemeViewer } from '../viewer/scheme3d.mjs';
import { candidateViewModel, candidateLayout, proposalSavePayload, describeProposalEdit, proposalNarrativeHTML } from './assistant_model.mjs';
import { openUploadModal } from './project.js';

export async function assistantView(ctx, scanId) {
  const canSave = isAdmin();
  let alive = true, request = null, viewer = null, proposals = [], fingerprints = null;
  let selected = 0, view = 'after', busy = false, saving = false, stale = false;
  let projectId = null;
  ctx.cleanup(() => { alive = false; request?.abort(); viewer?.dispose(); viewer = null; });
  ctx.actions.innerHTML = '';
  ctx.title([{ label: '房间工作台', href: `#/scans/${scanId}` }, { label: 'AI 改造助手', current: true }]);
  ctx.host.innerHTML = `<div class="view-scroll assistant-page">
    <header class="assistant-head"><div><div class="eyebrow">单房间 · 空间改造</div><h1>AI 改造助手</h1>
      <p class="muted">选择 2–3 种经正式链路验算的改造思路，保存前即可查看独立框架图。</p></div>
      <a class="btn" href="#/scans/${scanId}">返回房间</a></header>
    <form class="glass panel assistant-form" id="assistant-form">
      <label for="assistant-goal">补充改造目标 <span class="muted">（可选，最多 300 字）</span></label>
      <textarea class="textarea" id="assistant-goal" maxlength="300" rows="2" placeholder="例如：保留床，优先扩大门口通行空间"></textarea>
      <div class="row gap-3"><button class="btn btn-primary" id="assistant-generate" type="submit" disabled>${icon('wand', 15)} 生成改造方案</button>
        <button class="btn" id="assistant-cancel" type="button" hidden>取消请求</button>
        <span id="assistant-status" role="status" aria-live="polite">正在检查房间状态…</span></div>
      <p id="assistant-error" class="assistant-error" role="alert" hidden></p>
    </form>
    <div class="assistant-workspace" id="assistant-workspace" hidden>
      <aside class="assistant-candidates" id="assistant-candidates" aria-label="候选改造方案"></aside>
      <section class="glass panel assistant-preview"><div class="panel-head"><h2 class="panel-title">家居框架图 · <span id="assistant-view-label">改造后</span></h2>
        <div class="row gap-2"><button class="btn btn-sm" id="assistant-before" aria-pressed="false">改造前</button>
          <button class="btn btn-sm" id="assistant-after" aria-pressed="true">改造后</button>
          <button class="icon-btn" id="assistant-reset" aria-label="重置视角">${icon('target', 16)}</button></div></div>
        <div class="assistant-stage" id="assistant-stage" aria-label="所选方案的三维框架图"></div>
        <p class="muted assistant-legend">拖拽旋转 · 滚轮缩放 · 右键平移<br>紫色：墙 · 琥珀：门 · 薄荷：窗 · 玫瑰：家具<br>青色：改动 · 黄色：假设新增 · 红色：将移除</p>
      </section>
      <section class="glass panel assistant-evidence-panel">
        <button class="btn btn-primary" id="assistant-save">${icon('save', 15)} 保存此方案</button>
        <button class="btn" id="assistant-unsatisfied" type="button" aria-expanded="false">不满意，重新处理</button>
        <div id="assistant-retry-options" hidden>
          <p class="muted">方案不满意可补充上方目标再生成；原始数据不满意请重新上传新视频。旧扫描与已保存方案不会覆盖。</p>
          <div class="row gap-2" style="flex-wrap:wrap">
            <button class="btn" id="assistant-regenerate" type="button">只重新生成方案</button>
            <button class="btn" id="assistant-reupload" type="button" ${canSave ? '' : 'disabled'}>重新上传新视频</button>
          </div>
          ${canSave ? '' : '<p class="muted">重新上传与重建仅管理员可操作。</p>'}
        </div>
        <p class="muted" id="assistant-save-status" role="status"></p><div id="assistant-evidence"></div></section>
    </div>
  </div>`;
  const $ = (selector) => ctx.host.querySelector(selector);
  const status = (text) => { $('#assistant-status').textContent = text; };
  const error = (text = '') => { $('#assistant-error').hidden = !text; $('#assistant-error').textContent = text; };
  function controls() {
    $('#assistant-generate').disabled = busy || saving;
    $('#assistant-goal').disabled = busy || saving;
    $('#assistant-cancel').hidden = !busy;
    $('#assistant-form').setAttribute('aria-busy', String(busy));
    $('#assistant-save').disabled = !canSave || saving || stale || !proposals.length;
    $('#assistant-unsatisfied').disabled = busy || saving;
    $('#assistant-regenerate').disabled = busy || saving;
    $('#assistant-reupload').disabled = !canSave || !projectId || busy || saving;
    $('#assistant-save').innerHTML = canSave
      ? `${icon('save', 15)} ${saving ? '正在保存…' : '保存此方案'}`
      : '仅管理员可保存';
    $('#assistant-candidates').querySelectorAll('button').forEach((button) => { button.disabled = saving; });
  }
  function preview() {
    const proposal = proposals[selected];
    if (!proposal) return;
    const { layout, marks } = candidateLayout(proposal, view);
    viewer?.setLayout(layout, marks);
    $('#assistant-before').setAttribute('aria-pressed', String(view === 'before'));
    $('#assistant-after').setAttribute('aria-pressed', String(view === 'after'));
    $('#assistant-view-label').textContent = view === 'before' ? '改造前' : '改造后';
  }
  function select(index) {
    selected = index;
    $('#assistant-candidates').querySelectorAll('button').forEach((button, i) => {
      button.setAttribute('aria-pressed', String(i === selected));
      button.classList.toggle('is-selected', i === selected);
      button.querySelector('.eyebrow').textContent = `候选 ${i + 1} · ${i === selected ? '当前预览' : '选择预览'}`;
    });
    const proposal = proposals[selected];
    const measured = (value, unit) => { const result = formatMeasurement(value, unit); return `${result.text}${result.unit}`; };
    const changes = proposal.metric_changes.map((item) => `<li>${esc(item.name || item.metric_code)}：${esc(measured(item.before, item.unit))} → ${esc(measured(item.after, item.unit))}</li>`).join('');
    const risks = proposal.risk_changes.map((item) => `<li>${esc(item.name || item.metric_code)}：${esc(riskLevel(item.before).label)} → ${esc(riskLevel(item.after).label)}</li>`).join('');
    $('#assistant-evidence').innerHTML = `<h2>${esc(proposal.title)}</h2>
      <p class="assistant-score">正式评分 <b>${esc(fmtScore(proposal.comparison.before_score))}</b> → <b>${esc(fmtScore(proposal.comparison.after_score))}</b></p>
      <h3>具体改动</h3><ol>${proposal.edits.map((edit) => `<li>${esc(describeProposalEdit(edit))}</li>`).join('')}</ol>
      <h3>指标变化</h3>${changes ? `<ul>${changes}</ul>` : '<p>无可核查的指标变化。</p>'}
      <h3>风险变化</h3>${risks ? `<ul>${risks}</ul>` : '<p>风险等级未变化。</p>'}
      ${proposalNarrativeHTML(proposal, esc)}<p class="muted">${esc(proposal.disclaimer)}</p>`;
    preview();
  }
  async function generate(event) {
    event.preventDefault();
    if (busy || saving) return;
    const controller = new AbortController(); request = controller;
    busy = true; stale = false; proposals = []; fingerprints = null;
    $('#assistant-retry-options').hidden = true;
    $('#assistant-unsatisfied').setAttribute('aria-expanded', 'false');
    $('#assistant-workspace').hidden = true; controls(); error();
    status('正在生成并逐组验算；必要时会修订一次，请稍候…');
    try {
      const result = await api.generateRoomProposals(scanId, $('#assistant-goal').value.trim(), { signal: controller.signal });
      if (!alive || request !== controller) return;
      if (result.status !== 'ready' || result.proposals?.length < 2 || result.proposals?.length > 3) throw new Error(result.message || '当前资料不足以生成多个可信方案，请补充测量后重试。');
      proposals = result.proposals.map(candidateViewModel); fingerprints = result.input_fingerprints;
      selected = 0; view = 'after';
      $('#assistant-candidates').innerHTML = proposals.map((proposal, index) => `<button class="glass assistant-candidate" type="button" aria-pressed="${index === 0}">
        <span class="eyebrow">候选 ${index + 1} · ${index === 0 ? '当前预览' : '选择预览'}</span><strong>${esc(proposal.title)}</strong>
        <span>${esc(proposal.intent)}</span><span class="assistant-card-score">正式评分 ${esc(fmtScore(proposal.comparison.before_score))} → ${esc(fmtScore(proposal.comparison.after_score))}</span>
        <span class="tag">${proposal.requires_construction ? '涉及施工 · 须专业复核' : '空间调整'}${proposal.assumed_ids?.length ? ' · 含假设新增' : ''}</span>
      </button>`).join('');
      $('#assistant-candidates').querySelectorAll('button').forEach((button, index) => button.addEventListener('click', () => {
        if (saving) return;
        select(index);
      }));
      $('#assistant-workspace').hidden = false;
      if (!viewer) {
        try { viewer = createSchemeViewer($('#assistant-stage'), {}); }
        catch { $('#assistant-stage').innerHTML = '<p class="assistant-view-error">3D 视图初始化失败，请使用支持 WebGL 的浏览器重试。</p>'; }
      }
      select(0); status(`已生成 ${proposals.length} 个独立验算方案，选择后可预览与保存。`);
    } catch (err) {
      if (!alive || request !== controller) return;
      if (err.name === 'AbortError') status('已取消本次请求，可重新生成。');
      else { status('生成未完成'); error(err.message || '生成失败，请重试。'); }
    } finally {
      if (alive && request === controller) { busy = false; request = null; controls(); }
    }
  }
  $('#assistant-form').addEventListener('submit', generate);
  $('#assistant-unsatisfied').addEventListener('click', () => {
    const options = $('#assistant-retry-options');
    options.hidden = !options.hidden;
    $('#assistant-unsatisfied').setAttribute('aria-expanded', String(!options.hidden));
  });
  $('#assistant-regenerate').addEventListener('click', generate);
  $('#assistant-reupload').addEventListener('click', () => {
    if (!canSave || !projectId || busy || saving) return;
    openUploadModal(null, (newId) => { if (alive) navigate(`#/scans/${newId}`); }, null,
      { deferProcessing: true, projectId });
  });
  $('#assistant-cancel').addEventListener('click', () => {
    request?.abort(); request = null; busy = false; controls(); status('已取消本次请求，可重新生成。');
  });
  ['before', 'after'].forEach((side) => $(`#assistant-${side}`).addEventListener('click', () => { view = side; preview(); }));
  $('#assistant-reset').addEventListener('click', () => viewer?.resetView());
  $('#assistant-save').addEventListener('click', async () => {
    if (!canSave || saving || stale || !proposals.length) return;
    const controller = new AbortController(); request = controller;
    saving = true; controls(); error(); $('#assistant-save-status').textContent = '正在核对扫描指纹并保存…';
    try {
      const saved = await api.createScheme(scanId, proposalSavePayload(proposals[selected], fingerprints), { signal: controller.signal });
      if (!alive || request !== controller) return;
      navigate(`#/scans/${scanId}/schemes/${saved.id}`);
    } catch (err) {
      if (!alive || request !== controller) return;
      stale = err.status === 409;
      $('#assistant-save-status').textContent = '';
      error(err.name === 'AbortError' ? '保存请求已中止，请在方案库核对保存结果。' : err.message || '保存失败，请重试。');
    } finally {
      if (alive && request === controller) { saving = false; request = null; controls(); }
    }
  });
  try {
    const scan = await api.getScan(scanId);
    if (!alive) return;
    projectId = scan.project_id;
    const unavailable = !['video', 'photos'].includes(scan.capture_type) ? '小助手仅支持单房间扫描，整屋扫描暂不可用。'
      : scan.status !== 'done' ? '请等待房间扫描完成后生成方案。' : '';
    if (unavailable) { status(unavailable); return; }
    controls(); status('将依据该房间当前可评估风险生成方案。');
  } catch (err) {
    if (alive) { status('房间状态读取失败，请返回房间重试。'); error(err.message); }
  }
}
