"""生成受约束的中文模拟说明。"""
from __future__ import annotations

from assistant.local_llm import explain_with_local_model


def build_response(intent: dict, comparison: dict, changes: list[dict]) -> str:
    if changes:
        delta = comparison.get("score_delta")
        score_text = f"，预计评分变化 {delta:+.1f} 分" if isinstance(delta, (int, float)) else ""
        template = f"已在临时沙盒中模拟 {intent['action']}，有 {len(changes)} 项结构化指标发生变化{score_text}。"
    else:
        template = "这项改造可以作为定性安全建议，但当前正式评分规则没有对应量化指标，因此不会虚构分数变化。"
    generated = explain_with_local_model({"intent": intent, "comparison": comparison, "changes": changes})
    return generated or template
