"""生成受约束的中文模拟说明（纯模板，不调用任何本地/远程 LLM，避免额外 token 消耗）。"""
from __future__ import annotations


def build_response(intent: dict, comparison: dict, changes: list[dict],
                   risk_changes: list[dict] | None = None) -> str:
    """把结构化模拟结果拼成一句话说明；没有量化变化时诚实说明，不虚构分数。"""
    action = intent.get("action", "")
    delta = comparison.get("score_delta")
    before = comparison.get("before_score")
    after = comparison.get("after_score")
    if isinstance(delta, (int, float)) and isinstance(before, (int, float)) and isinstance(after, (int, float)):
        head = f"模拟完成：当前 {before:.1f} 分 → 预计 {after:.1f} 分（{delta:+.1f}）"
    else:
        head = "模拟完成：当前数据不足以产生正式评分"
    parts = [head]
    if changes:
        detail = "；".join(
            f"{item['name']} {item['before']}{item.get('unit') or ''} → {item['after']}{item.get('unit') or ''}"
            for item in changes[:5])
        parts.append(f"变化指标：{detail}")
    if risk_changes:
        detail = "；".join(
            f"{item['name']} 风险 {item['before']} → {item['after']}"
            for item in risk_changes[:5])
        parts.append(f"风险等级变化：{detail}")
    if action == "APPLY_SUGGESTIONS":
        parts.append("（勾选建议已在内存沙盒中执行并重算评分）")
    if not changes and not risk_changes:
        parts.append("该建议当前只能提供定性建议，暂时无法精确计算评分变化。")
    return "。".join(parts) + "。"
