"""官方评估链路的唯一内存入口：改造模拟与正式报告共用同一套算法。"""
from __future__ import annotations

from pipeline.risk_assessment import build_risk_assessment
from pipeline.scheme_state import apply_edits
from pipeline.space_foundation import analyze_structure_passages, build_spatial_foundation
from pipeline.spatial_assessment_inputs import build_spatial_assessment_inputs

def assess_scheme(measurements: dict, structure: dict) -> dict:
    """只读内存输入，跑正式链路，返回指标/风险/分数。"""
    passage = analyze_structure_passages(measurements, structure)
    foundation = build_spatial_foundation(measurements, structure, passage)
    payload = build_spatial_assessment_inputs(measurements, passage, foundation)
    assessment = build_risk_assessment(payload)
    return {
        "metrics": payload.get("metrics", []),
        "paths": payload.get("paths", []),
        "risks": assessment.get("risks", []),
        "overall": assessment.get("overall", {}),
        "category_scores": assessment.get("category_scores", {}),
        "top_risks": assessment.get("top_risks", []),
    }


def build_scheme(measurements: dict, structure: dict, edits: list[dict]) -> dict:
    from pipeline.scheme_layout import build_layout, build_layout_objects

    state = apply_edits(measurements, structure, edits or [])
    result = assess_scheme(state["measurements"], state["structure"])
    return {
        **state,
        **result,
        "layout_objects": build_layout_objects(state["structure"]),
        "layout_before": build_layout(measurements, structure),
        "layout_after": build_layout(state["measurements"], state["structure"]),
        "scope": {
            "simulation": True,
            "raw_media_accessed": False,
            "structured_inputs_only": True,
            "contains_assumed_objects": bool(state["assumed_ids"]),
        },
    }


def _metric_map(assessment: dict) -> dict:
    return {item["metric_code"]: item for item in assessment.get("metrics", [])}


def diff_assessments(before: dict, after: dict) -> dict:
    """前后对比：分数、指标变化、风险等级变化。"""
    before_score = (before.get("overall") or {}).get("score")
    after_score = (after.get("overall") or {}).get("score")
    delta = None if before_score is None or after_score is None else round(after_score - before_score, 1)
    before_metrics, after_metrics = _metric_map(before), _metric_map(after)
    metric_changes = []
    for code, item in after_metrics.items():
        old = before_metrics.get(code)
        if old is None or old.get("status") == "not_evaluable":
            continue
        if old.get("value") != item.get("value"):
            metric_changes.append({"metric_code": code, "name": item.get("name"), "unit": item.get("unit"),
                                   "before": old.get("value"), "after": item.get("value")})
    before_levels = {r["metric_code"]: r.get("risk_level") for r in before.get("risks", [])}
    risk_changes = []
    for risk in after.get("risks", []):
        old = before_levels.get(risk["metric_code"])
        if old is not None and old != risk.get("risk_level"):
            risk_changes.append({"metric_code": risk["metric_code"], "name": risk.get("risk_name"),
                                 "before": old, "after": risk.get("risk_level")})
    return {
        "comparison": {"before_score": before_score, "after_score": after_score, "score_delta": delta},
        "metric_changes": metric_changes,
        "risk_changes": risk_changes,
    }