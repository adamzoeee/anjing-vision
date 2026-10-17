import { diffLayout } from '../viewer/scheme3d.mjs';

export function candidateViewModel(proposal) {
  return {
    ...proposal,
    title: proposal?.title || '改造方案', intent: proposal?.intent || '',
    edits: Array.isArray(proposal?.edits) ? proposal.edits : [],
    comparison: proposal?.comparison || {},
    metric_changes: proposal?.metric_changes || [], risk_changes: proposal?.risk_changes || [],
    evidence: proposal?.evidence || {}, limitations: proposal?.limitations || [],
  };
}

export function candidateLayout(proposal, view = 'after') {
  const diff = diffLayout(proposal.layout_before, proposal.layout_after);
  return view === 'before'
    ? { layout: proposal.layout_before, marks: { changed: diff.changed, removed: diff.removed } }
    : { layout: proposal.layout_after, marks: { changed: diff.changed, added: diff.added } };
}

export function proposalSavePayload(proposal, fingerprints) {
  if (!fingerprints || !Object.keys(fingerprints).length) throw new Error('缺少扫描输入指纹，请重新生成方案');
  return {
    name: proposal.title, source: 'ai', edits: proposal.edits, based_on: { ...fingerprints },
    proposal_meta: { title: proposal.title, intent: proposal.intent,
      risk_codes: proposal.risk_codes || [], limitations: proposal.limitations || [] },
  };
}

export function describeProposalEdit(edit) {
  const target = edit.target || edit.label || '物体';
  if (edit.op === 'move') return `移动 ${target}：X ${edit.dx ?? 0} m，Y ${edit.dy ?? 0} m`;
  if (edit.op === 'add') return `新增 ${target}（假设放置，尺寸 ${(edit.size || []).join(' × ')} m）`;
  if (edit.op === 'remove') return `移除 ${target}`;
  if (edit.op === 'rotate') return `旋转 ${target} ${90 * (edit.quarter_turns || 1)}°`;
  if (edit.op === 'resize_door') return `调整门宽 ${edit.width_m} m（需施工复核）`;
  return `${edit.op || '编辑'} ${target}`;
}

// All strings, including server evidence, pass through the caller's text escaper.
export function proposalNarrativeHTML(meta, esc) {
  if (!meta?.intent) return '';
  const facts = meta.evidence?.facts || {};
  const verification = meta.evidence?.verification || {};
  const comparison = verification.comparison || {};
  return `<section class="assistant-narrative">
    <h3>AI 设计意图</h3><p>${esc(meta.intent)}</p>
    <h3>扫描事实</h3>
    <p>原始正式评分：${esc(facts.score ?? '不可评估')}</p>
    <ul>${(facts.risks || []).map((risk) => `<li>${esc(risk.risk_name || risk.metric_code)}：${esc(risk.measured_value ?? '不可评估')}${esc(risk.unit || '')} · ${esc(risk.assessment_status || risk.risk_level || '')}</li>`).join('')}</ul>
    <h3>正式评估链路验算</h3>
    <p>依据本方案独立布局重算。${comparison.after_score != null ? `正式评分 ${esc(comparison.before_score ?? '不可评估')} → ${esc(comparison.after_score)}` : ''}</p>
    <h3>模拟边界</h3><ul>${(meta.limitations || []).map((text) => `<li>${esc(text)}</li>`).join('')}</ul>
  </section>`;
}
