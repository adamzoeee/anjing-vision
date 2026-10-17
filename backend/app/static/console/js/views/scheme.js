/* 方案可视化页：改造后的家居框架图（3D）+ 平面图 + 通行图，三块都支持改造前/后对照。
 *
 * 数据：GET /api/assistant/scans/{id}/schemes/{sid}（layout_before/after + 评分变化）；
 * 图纸：改造后由服务端按派生状态渲染（/plan.png、/passage.png），改造前直接复用
 * 扫描自己的报告资源（structure_plan.png、passage_plan.png）——全部只读、不落盘。 */

import { api, fetchAsset } from '../api.js?v=house-scan-1';
import { icon } from '../icons.js';
import { esc, toast, fmtScore, riskLevel, emptyState, lightbox } from '../ui.js';
import { navigate } from '../app.js';
import { createSchemeViewer, diffLayout } from '../viewer/scheme3d.mjs';
import { drawPlanPNG } from './room.js';
import { describeProposalEdit, proposalNarrativeHTML } from './assistant_model.mjs';

const EDIT_LABEL = {
  move: '移动', add: '新增', remove: '移除', resize_door: '调整门洞宽度',
};

function describeEdit(edit) {
  if (edit?.op === 'rotate') return describeProposalEdit(edit);
  const op = EDIT_LABEL[edit?.op] || edit?.op || '编辑';
  const target = edit?.target || edit?.label || '物体';
  if (edit?.op === 'move') {
    const parts = [];
    if (Number.isFinite(edit.dx) && Math.abs(edit.dx) > 1e-6) parts.push(`X ${edit.dx > 0 ? '+' : ''}${Math.round(edit.dx * 100)}cm`);
    if (Number.isFinite(edit.dy) && Math.abs(edit.dy) > 1e-6) parts.push(`Y ${edit.dy > 0 ? '+' : ''}${Math.round(edit.dy * 100)}cm`);
    return `${op} ${target}（${parts.join('，') || '原地'}）`;
  }
  if (edit?.op === 'add') {
    const size = Array.isArray(edit.size) ? edit.size.map((v) => Math.round(v * 100)).join('×') : '';
    return `${op}${edit.label ? ` ${edit.label}` : ''}${size ? `（${size}cm，假设放置）` : '（假设放置）'}`;
  }
  if (edit?.op === 'resize_door') {
    return `${op}：${edit.width_m ?? 0.9}m（需施工）`;
  }
  return `${op} ${target}`;
}

export async function schemeView(ctx, scanId, schemeId) {
  let viewer = null;
  ctx.actions.innerHTML = '';
  ctx.cleanup(() => {
    viewer?.dispose();
    viewer = null;
  });

  ctx.title([
    { label: '房间工作台', href: `#/scans/${scanId}` },
    { label: `方案 #${schemeId}`, current: true },
  ]);
  ctx.host.innerHTML = `
    <div class="view-scroll scheme-page">
      <div class="glass panel rise" style="max-width:560px;margin:80px auto">
        <div class="panel-head"><div class="panel-title">${icon('wand', 15)} 正在读取方案…</div></div>
        <div class="skeleton-line shimmer" style="width:70%"></div>
      </div>
    </div>`;

  let detail = null;
  try {
    detail = await api.getScheme(scanId, schemeId);
  } catch (error) {
    ctx.host.innerHTML = `
      <div class="view-scroll scheme-page">
        <div class="glass panel rise">${emptyState({
          iconName: 'save', title: '方案读取失败', desc: error?.message || '请返回房间工作台重试。',
        })}</div>
      </div>`;
    return;
  }

  const plan = detail.plan || {};
  const comparison = detail.comparison || {};
  const edits = Array.isArray(plan.edits) ? plan.edits : [];
  const metricChanges = Array.isArray(detail.metric_changes) ? detail.metric_changes : [];
  const riskChanges = Array.isArray(detail.risk_changes) ? detail.risk_changes : [];
  const diff = diffLayout(detail.layout_before, detail.layout_after);
  const assumedCount = (detail.layout_after?.objects || []).filter((item) => item.assumed).length;
  const schemeName = plan.name || `方案 #${schemeId}`;

  ctx.title([
    { label: '房间工作台', href: `#/scans/${scanId}` },
    { label: schemeName, current: true },
  ]);

  ctx.host.innerHTML = `
    <div class="view-scroll scheme-page">
      <div class="scheme-head rise">
        <div class="sh-left">
          <div class="row gap-3">
            <h1>${esc(schemeName)}</h1>
            ${detail.stale ? '<span class="badge warn">基于旧数据</span>' : '<span class="badge info">沙盒模拟</span>'}
          </div>
          <div class="muted" style="font-size:12.5px;margin-top:4px">
            房间 #${scanId} · ${icon('wand', 12)} AI 改造助手${edits.length ? ` · 改造 ${edits.length} 处` : ' · 未修改布局'}
            ${assumedCount ? ` · 含 ${assumedCount} 件假设放置` : ''}
          </div>
        </div>
        <div class="sh-score">
          <div class="score-chip"><div class="label">${detail.stale ? '当前重算 · 改造前' : '改造前'}</div><div class="value">${fmtScore(comparison.before_score)}</div></div>
          <div class="score-arrow">${icon('chevronRight', 18)}</div>
          <div class="score-chip after"><div class="label">${detail.stale ? '当前重算 · 改造后' : '改造后'}</div><div class="value">${fmtScore(comparison.after_score)}</div></div>
          ${Number.isFinite(Number(comparison.score_delta)) ? `
            <span class="badge ${comparison.score_delta >= 0 ? 'ok' : 'bad'}">${comparison.score_delta >= 0 ? '+' : ''}${comparison.score_delta}</span>` : ''}
        </div>
        <div class="sh-actions">
          <button class="btn" id="btn-scheme-back">${icon('home', 15)} 返回房间</button>
          <button class="btn btn-primary" id="btn-scheme-export">${icon('download', 15)} 导出 PDF</button>
        </div>
      </div>

      <div class="scheme-grid">
        ${(detail.proposal_meta || plan.proposal_meta)?.intent ? `<div class="glass panel rise scheme-span2">${proposalNarrativeHTML(detail.proposal_meta || plan.proposal_meta, esc, 'saved')}</div>` : ''}
        <div class="glass panel rise scheme-span2">
          <div class="panel-head">
            <div class="panel-title">${icon('box', 15)} 家居框架图（3D）</div>
            <div class="panel-actions">
              <div class="plan-tabs" id="tabs-3d" style="min-width:168px">
                <button class="plan-tab active" data-view="after" role="tab">改造后</button>
                <button class="plan-tab" data-view="before" role="tab">改造前</button>
              </div>
              <button class="icon-btn" id="btn-3d-rotate" data-tip="自动旋转" aria-label="自动旋转">${icon('rotate', 16)}</button>
              <button class="icon-btn" id="btn-3d-reset" data-tip="重置视角" aria-label="重置视角">${icon('target', 16)}</button>
            </div>
          </div>
          <div class="scheme-stage" id="scheme-stage"></div>
          <div class="scheme-legend">
            <span style="color:#9b8cff"><span class="sw"></span>墙</span>
            <span style="color:#ffc24b"><span class="sw"></span>门</span>
            <span style="color:#6ee7b7"><span class="sw"></span>窗</span>
            <span style="color:#ff9db1"><span class="sw"></span>家具</span>
            <span style="color:#64ffda"><span class="sw"></span>本方案改动</span>
            <span style="color:#ffd166"><span class="sw"></span>新增（假设放置）</span>
            <span style="color:#ff7b7b"><span class="sw"></span>将移除（对照改造前）</span>
            <span class="muted">拖拽旋转 · 滚轮缩放 · 右键平移</span>
          </div>
        </div>

        <div class="glass panel rise">
          <div class="panel-head">
            <div class="panel-title">${icon('grid', 15)} 平面图</div>
            <div class="panel-actions">
              <div class="plan-tabs" id="tabs-plan" style="min-width:168px">
                <button class="plan-tab active" data-view="after" role="tab">改造后</button>
                <button class="plan-tab" data-view="before" role="tab">改造前</button>
              </div>
            </div>
          </div>
          <div class="plan-frame" id="frame-plan"><div class="plan-loading">${icon('image', 22)}<div style="margin-top:8px">加载平面图…</div></div></div>
        </div>

        <div class="glass panel rise">
          <div class="panel-head">
            <div class="panel-title">${icon('activity', 15)} 通行图</div>
            <div class="panel-actions">
              <div class="plan-tabs" id="tabs-passage" style="min-width:168px">
                <button class="plan-tab active" data-view="after" role="tab">改造后</button>
                <button class="plan-tab" data-view="before" role="tab">改造前</button>
              </div>
            </div>
          </div>
          <div class="plan-frame" id="frame-passage"><div class="plan-loading">${icon('image', 22)}<div style="margin-top:8px">加载通行图…</div></div></div>
        </div>

        <div class="glass panel rise scheme-span2">
          <div class="panel-head">
            <div class="panel-title">${icon('wand', 15)} 改造了什么</div>
            <div class="panel-actions panel-sub">${esc(detail.disclaimer || '沙盒模拟 · 未修改真实数据')}</div>
          </div>
          <div class="changes-grid">
            <div class="changes-block">
              <div class="eyebrow">编辑清单</div>
              ${edits.length
                ? `<ol class="edit-list">${edits.map((edit) => `<li>${esc(describeEdit(edit))}</li>`).join('')}</ol>`
                : '<div class="faint" style="font-size:12.5px">该方案未修改任何布局（基线对照）。</div>'}
            </div>
            <div class="changes-block">
              <div class="eyebrow">指标变化</div>
              ${metricChanges.length ? `<div class="change-chips">${metricChanges.map((item) => `
                <span class="change-chip">${esc(item.name || item.metric_code)}
                  <b>${esc(String(item.before ?? '—'))}</b> → <b class="up">${esc(String(item.after ?? '—'))}</b>${esc(item.unit || '')}
                </span>`).join('')}</div>`
                : '<div class="faint" style="font-size:12.5px">关键指标没有变化。</div>'}
            </div>
            <div class="changes-block">
              <div class="eyebrow">风险变化</div>
              ${riskChanges.length ? `<div class="change-chips">${riskChanges.map((item) => {
                const before = riskLevel(item.before); const after = riskLevel(item.after);
                return `<span class="change-chip">${esc(item.name || item.metric_code)}
                  <span class="badge ${before.tone === 'neutral' ? '' : before.tone}">${before.label}</span> →
                  <span class="badge ${after.tone === 'neutral' ? '' : after.tone}">${after.label}</span></span>`;
              }).join('')}</div>`
                : '<div class="faint" style="font-size:12.5px">风险等级没有变化。</div>'}
            </div>
          </div>
        </div>
      </div>
    </div>`;

  /* —— 3D 家居框架图 —— */
  const stage = document.getElementById('scheme-stage');
  try {
    viewer = createSchemeViewer(stage, {});
  } catch (error) {
    stage.innerHTML = `<div class="plan-loading">${icon('box', 22)}<div style="margin-top:8px">${esc(error?.message || '3D 视图初始化失败')}</div></div>`;
  }

  function showLayout(view) {
    if (!viewer) return;
    const layout = view === 'before' ? detail.layout_before : detail.layout_after;
    const mark = view === 'before'
      ? { changed: diff.changed, removed: diff.removed }
      : { changed: diff.changed, added: diff.added };
    viewer.setLayout(layout, mark);
  }
  showLayout('after');

  document.getElementById('tabs-3d')?.querySelectorAll('.plan-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.getElementById('tabs-3d').querySelectorAll('.plan-tab')
        .forEach((node) => node.classList.toggle('active', node === tab));
      showLayout(tab.dataset.view);
    });
  });
  const rotateBtn = document.getElementById('btn-3d-rotate');
  rotateBtn?.addEventListener('click', () => {
    const on = !rotateBtn.classList.contains('is-active');
    rotateBtn.classList.toggle('is-active', on);
    viewer?.setAutoRotate(on);
  });
  document.getElementById('btn-3d-reset')?.addEventListener('click', () => viewer?.resetView());

  /* —— 平面图 / 通行图（改造后 = 服务端按方案渲染；改造前 = 扫描自己的报告资源） —— */
  const activeView = { plan: 'after', passage: 'after' };

  function assetPath(kind, view) {
    if (view === 'after') {
      return `/api/assistant/scans/${scanId}/schemes/${schemeId}/${kind === 'passage' ? 'passage' : 'plan'}.png`;
    }
    return `/api/preview/${scanId}/${kind === 'passage' ? 'passage_plan.png' : 'structure_plan.png'}`;
  }

  async function loadImage(kind, frameId) {
    const frame = document.getElementById(frameId);
    if (!frame) return;
    const view = activeView[kind];
    const label = `${kind === 'passage' ? '通行图' : '平面图'}（${view === 'after' ? '改造后' : '改造前'}）`;
    frame.onclick = null;
    frame.innerHTML = `<div class="plan-loading">${icon('image', 22)}<div style="margin-top:8px">加载${label}…</div></div>`;
    try {
      const url = await fetchAsset(assetPath(kind, view));
      if (activeView[kind] !== view) return;   // 切换太快：丢弃过期结果
      const img = document.createElement('img');
      img.src = url;
      img.alt = label;
      frame.innerHTML = '';
      frame.appendChild(img);
      frame.onclick = () => lightbox(url, label);
    } catch (error) {
      if (activeView[kind] !== view) return;
      frame.innerHTML = `<div class="plan-loading">${icon('image', 22)}<div style="margin-top:8px">${esc(label)}暂不可用</div></div>`;
    }
  }

  [['plan', 'tabs-plan', 'frame-plan'], ['passage', 'tabs-passage', 'frame-passage']]
    .forEach(([kind, tabsId, frameId]) => {
      document.getElementById(tabsId)?.querySelectorAll('.plan-tab').forEach((tab) => {
        tab.addEventListener('click', () => {
          document.getElementById(tabsId).querySelectorAll('.plan-tab')
            .forEach((node) => node.classList.toggle('active', node === tab));
          activeView[kind] = tab.dataset.view;
          loadImage(kind, frameId);
        });
      });
      loadImage(kind, frameId);
    });

  /* —— 顶部操作 —— */
  document.getElementById('btn-scheme-back')?.addEventListener('click', () => navigate(`#/scans/${scanId}`));
  document.getElementById('btn-scheme-export')?.addEventListener('click', async (event) => {
    const btn = event.currentTarget;
    btn.disabled = true;
    toast('info', '正在生成方案图纸…');
    try {
      const png = await drawPlanPNG(detail.layout_after, schemeName);
      const pdfBlob = await api.exportSchemePdf(scanId, schemeId, png);
      const url = URL.createObjectURL(pdfBlob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `anjing_scheme_${schemeId}.pdf`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      toast('ok', '方案 PDF 已导出', '包含前后评分与指标变化');
    } catch (error) {
      toast('error', '导出失败', error?.message || '');
    } finally {
      btn.disabled = false;
    }
  });
}
