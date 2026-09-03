"""改造模拟前后对比。"""
from __future__ import annotations


def compare_assessments(before: dict, after: dict) -> dict:
    before_score = (before.get("overall") or {}).get("score")
    after_score = (after.get("overall") or {}).get("score")
    delta = None if before_score is None or after_score is None else round(after_score - before_score, 1)
    before_risks = {item["metric_code"]: item for item in before.get("risks", [])}
    improvements = []
    order = {None: -1, "high": 0, "medium": 1, "low": 2}
    for item in after.get("risks", []):
        old = before_risks.get(item["metric_code"])
        if old and order.get(item.get("risk_level"), -1) > order.get(old.get("risk_level"), -1):
            improvements.append({
                "metric_code": item["metric_code"],
                "name": item["risk_name"],
                "before": old.get("risk_level"),
                "after": item.get("risk_level"),
            })
    return {
        "before_score": before_score,
        "after_score": after_score,
        "score_delta": delta,
        "improvements": improvements,
    }
