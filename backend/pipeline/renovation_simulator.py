"""纯内存的结构化改造模拟，不写回真实房间、点云或报告。"""
from __future__ import annotations

from copy import deepcopy

from pipeline.risk_assessment import build_risk_assessment


_GREEN_TARGETS = {
    "main_passage_width": 0.95, "minimum_passage_width": 0.90,
    "furniture_spacing": 0.65, "bedside_clearance": 0.65,
    "bed_surrounding_space": 0.65, "entrance_space": 1.60,
    "crowding": 0.40, "path_continuity": True,
    "path_obstruction": False, "main_activity_area_safety": True,
}


def _metric_map(payload: dict) -> dict[str, dict]:
    return {item["metric_code"]: item for item in payload.get("metrics", [])}


def _set_metric(metrics: dict[str, dict], code: str, value, changes: list[dict]) -> None:
    item = metrics.get(code)
    if not item:
        return
    before = item.get("value")
    if isinstance(value, float) and isinstance(before, (int, float)):
        if code == "crowding":
            value = min(float(before), value)
        else:
            value = max(float(before), value)
    item.update({
        "value": value,
        "status": "derived",
        "confidence": min(float(item.get("confidence") or 0.7), 0.7),
        "reason": None,
        "source": {"simulation": True, "basis": "structured_what_if"},
    })
    if before != value:
        changes.append({"metric_code": code, "before": before, "after": value})


def simulate(metric_payload: dict, intent: dict) -> dict:
    simulated = deepcopy(metric_payload)
    metrics = _metric_map(simulated)
    changes: list[dict] = []
    action, target = intent["action"], intent["target"]

    if action in {"APPLY_ALL", "BEST"}:
        codes = list(_GREEN_TARGETS)
    elif action == "REMOVE" or (action == "MOVE" and intent.get("direction") == "离开通道"):
        codes = [
            "main_passage_width", "minimum_passage_width", "furniture_spacing",
            "entrance_space", "crowding", "path_continuity", "path_obstruction",
        ]
        if target == "bed":
            codes += ["bedside_clearance", "bed_surrounding_space"]
    elif action == "MOVE":
        codes = ["furniture_spacing", "bedside_clearance", "bed_surrounding_space"]
    elif action == "RESIZE":
        codes = ["furniture_spacing", "crowding", "main_passage_width"]
    else:  # ADD：扶手、夜灯、防滑垫属于定性建议，当前正式指标不虚构分数。
        codes = []

    for code in codes:
        _set_metric(metrics, code, _GREEN_TARGETS[code], changes)
    after = build_risk_assessment(simulated)
    return {"metric_payload": simulated, "assessment": after, "changes": changes}
