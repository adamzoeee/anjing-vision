"""建议求解器：候选生成 → 官方链路验证 → 取达标且改动最小的解。

原则：任何展示出来的分数都必须来自 build_scheme（正式链路）的实算结果；
无解时返回 shortfall，不虚构提升。
"""
from __future__ import annotations

import time

from pipeline.scheme_assessment import build_scheme

STEP_M = 0.10
MAX_BUDGET = 300
AXES = ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0))
CONSTRUCTION_METRICS = {"door_width"}
LEVEL_RANK = {None: 0, "low": 0, "medium": 1, "high": 2}
MOVABLE_KINDS = {"furniture", "obstacle"}


def _movable_objects(measurements: dict) -> list[dict]:
    return [o for o in measurements.get("objects", []) if o.get("length_m") and o.get("width_m")]


def candidate_edits(measurements: dict, structure: dict, metric_code: str,
                    *, max_steps: int = 10) -> list[list[dict]]:
    """生成候选编辑：平移（4 轴 × 步长）→ 搬出房间 → 放宽门洞。"""
    candidates: list[list[dict]] = []
    if metric_code in CONSTRUCTION_METRICS:
        width = max(float(o.get("width_m") or 0.0) for o in measurements.get("openings", [])
                    if o.get("type") == "door") if any(
            o.get("type") == "door" for o in measurements.get("openings", [])) else 0.0
        if width and width < 0.90:
            candidates.append([{"op": "resize_door", "width_m": 0.90}])
    for obj in _movable_objects(measurements):
        oid = str(obj.get("instance_id") or obj.get("id"))
        for distance in (STEP_M * (i + 1) for i in range(max_steps)):
            for dx, dy in AXES:
                candidates.append([{"op": "move", "target": oid,
                                    "dx": round(dx * distance, 3), "dy": round(dy * distance, 3)}])
        candidates.append([{"op": "remove", "target": oid}])
    return candidates


def _metric_value(assessment: dict, metric_code: str) -> float | None:
    item = next((x for x in assessment.get("metrics", []) if x["metric_code"] == metric_code), None)
    return None if item is None else item.get("value")


def _risk_level(assessment: dict, metric_code: str) -> str | None:
    item = next((x for x in assessment.get("risks", []) if x["metric_code"] == metric_code), None)
    return None if item is None else item.get("risk_level")


def _risk_name(assessment: dict, metric_code: str) -> str:
    item = next((x for x in assessment.get("risks", []) if x["metric_code"] == metric_code), None)
    return (item or {}).get("risk_name") or metric_code


def _displacement(edits: list[dict]) -> float:
    return sum(abs(float(e.get("dx") or 0.0)) + abs(float(e.get("dy") or 0.0)) for e in edits) + len(edits) * STEP_M


def solve_metric(measurements: dict, structure: dict, metric_code: str,
                 *, budget: int = MAX_BUDGET) -> dict:
    """为单个风险求解；返回 4.3 约定的结果结构。"""
    started = time.perf_counter()
    baseline = build_scheme(measurements, structure, [])
    before_value, before_level = _metric_value(baseline, metric_code), _risk_level(baseline, metric_code)
    before_score = (baseline.get("overall") or {}).get("score")

    best_solved = None
    best_partial = None
    evaluations = 0
    budget_exhausted = False

    for edits in candidate_edits(measurements, structure, metric_code):
        if evaluations >= budget:
            budget_exhausted = True
            break
        result = build_scheme(measurements, structure, edits)
        if result["conflicts"]:
            continue
        evaluations += 1
        level = _risk_level(result, metric_code)
        score = (result.get("overall") or {}).get("score")
        candidate = {
            "edits": edits,
            "value": _metric_value(result, metric_code),
            "level": level,
            "score": score,
            "requires_construction": any(e.get("op") == "resize_door" for e in edits),
        }
        if level is None or LEVEL_RANK.get(level, 9) == 0:
            key = (candidate["requires_construction"], _displacement(edits))
            if best_solved is None or key < best_solved[0]:
                best_solved = (key, candidate)
        partial_key = (-(score or 0), LEVEL_RANK.get(level, 9))
        if best_partial is None or partial_key > best_partial[0]:
            best_partial = (partial_key, candidate)

    chosen = (best_solved or best_partial or (None, None))[1]
    solved = best_solved is not None
    if chosen is None:
        return {
            "metric_code": metric_code,
            "risk_name": _risk_name(baseline, metric_code),
            "solved": False,
            "edits": [],
            "requires_construction": False,
            "before": {"value": before_value, "level": before_level},
            "after": {"value": before_value, "level": before_level},
            "score": {"before": before_score, "after": before_score},
            "shortfall": "当前房间结构下没有可用的家具调整候选方案。",
            "evaluations": evaluations,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "budget_exhausted": budget_exhausted,
        }

    shortfall = None
    if not solved:
        unit = next((x.get("unit") for x in baseline.get("metrics", []) if x["metric_code"] == metric_code), "") or ""
        shortfall = (f"已尝试 {evaluations} 个候选方案仍未达标："
                     f"目标指标 {chosen['value']}{unit}（{chosen['level'] or 'low'}）。"
                     "当前房间条件下需要搬出家具或施工改造才能达成。")

    return {
        "metric_code": metric_code,
        "risk_name": _risk_name(baseline, metric_code),
        "solved": solved,
        "edits": chosen["edits"],
        "requires_construction": chosen["requires_construction"],
        "before": {"value": before_value, "level": before_level},
        "after": {"value": chosen["value"], "level": chosen["level"]},
        "score": {"before": before_score, "after": chosen["score"]},
        "shortfall": shortfall,
        "evaluations": evaluations,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "budget_exhausted": budget_exhausted,
    }
