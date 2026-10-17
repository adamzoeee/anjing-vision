"""Validate model edits and independently assess each room proposal in memory."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Callable

from pipeline.scheme_assessment import build_scheme, diff_assessments
from pipeline.scheme_state import _corners, apply_edits, object_id, overlap_depth

DISCLAIMER = "沙盒模拟 · 非实测；结果来自正式评估链路重算，实施前须现场复核。"
LIMITATIONS = ["仅基于现有结构化测量，未读取原始影像或点云。",
               "模拟不能判断承重、水电、施工许可或现场实际尺度。",
               "不可评估指标不代表安全，也不作为改善证据。"]
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


class ProposalRejected(ValueError):
    """A candidate failed verification; reason is a sanitized machine code."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


_Rejected = ProposalRejected


def _number(low: float, high: float, *, integer=False) -> dict:
    return {"type": "integer" if integer else "number", "minimum": low, "maximum": high}


def _edit_schema(measurements: dict, structure: dict) -> dict:
    bounds = structure["room"]["bounds_xy"]
    lo, hi = bounds["min"], bounds["max"]
    spans = [hi[i] - lo[i] for i in range(2)]
    height = float(structure["room"].get("height_m") or measurements["room"]["height_m"])
    if any(not math.isfinite(v) or v <= 0 for v in [*spans, height]):
        raise ValueError("Invalid room dimensions")
    ids = sorted({object_id(o) for o in measurements.get("objects", []) if object_id(o)})
    target = {"type": "string", "enum": ids}
    vector = lambda limits: {"type": "array", "minItems": 3, "maxItems": 3,
                             "prefixItems": [_number(a, b) for a, b in limits]}
    fields = {
        "move": {"target": target, "dx": _number(-min(spans[0], 10), min(spans[0], 10)),
                 "dy": _number(-min(spans[1], 10), min(spans[1], 10))},
        "remove": {"target": target},
        "rotate": {"target": target, "quarter_turns": _number(1, 3, integer=True)},
        "add": {"label": {"type": "string", "enum": ["bed", "chair", "desk", "table", "cabinet", "wardrobe", "sofa", "object"]},
                "center": vector([(lo[0], hi[0]), (lo[1], hi[1]), (0, height)]),
                "size": vector([(0.05, min(spans[0], 10)), (0.05, min(spans[1], 10)), (0.05, min(height, 5))])},
    }
    # The existing simulator applies resize_door to every door, not to one ID.
    # Advertise this explicitly rather than pretending a target ID is respected.
    if any(o.get("type") == "door" for o in measurements.get("openings", [])):
        fields["resize_door"] = {"width_m": _number(0.5, min(1.5, *spans))}
    out = {}
    for op, properties in fields.items():
        properties = {"op": {"type": "string", "enum": [op]}, **properties}
        out[op] = {"type": "object", "properties": properties,
                   "required": list(properties), "additionalProperties": False}
    if "resize_door" in out:
        out["resize_door"]["description"] = "Changes ALL room doors; construction assumption requiring on-site review."
    return out


def _validate(value, schema: dict) -> None:
    kind = schema["type"]
    if kind in {"number", "integer"}:
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not schema["minimum"] <= value <= schema["maximum"]
                or kind == "integer" and value != int(value)):
            raise _Rejected("invalid_number")
    elif kind == "string":
        if not isinstance(value, str) or value not in schema["enum"]:
            raise _Rejected("invalid_edit")
    elif kind == "array":
        if not isinstance(value, list) or len(value) != 3:
            raise _Rejected("invalid_edit")
        for component, item_schema in zip(value, schema["prefixItems"]):
            _validate(component, item_schema)
    elif kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["required"]):
            raise _Rejected("invalid_edit")
        for key, item_schema in schema["properties"].items():
            _validate(value[key], item_schema)


def _context(measurements: dict, structure: dict, baseline: dict, goal: str | None) -> dict:
    def selected(items, keys):
        return [{"id": object_id(item), **{key: deepcopy(item[key]) for key in keys if key in item}}
                for item in items]
    return {
        "goal": goal,
        "room": deepcopy(baseline["layout_before"]["room"]),
        "objects": selected(measurements.get("objects", []),
                            ("type", "center", "length_m", "width_m", "height_m", "rotation_z_deg", "measurement_status")),
        "openings": selected(measurements.get("openings", []),
                             ("type", "center", "width_m", "height_m", "rotation_z_deg", "measurement_status")),
        "obstacles": deepcopy(baseline["layout_before"]["objects"]),
        "walls": deepcopy(baseline["layout_before"]["walls"]),
        "risks": [{key: deepcopy(risk.get(key)) for key in
                   ("risk_code", "metric_code", "measured_value", "unit", "threshold", "risk_level", "related_object_ids")}
                  for risk in baseline["risks"] if risk.get("assessment_status") == "evaluated"],
        "score": baseline["overall"].get("score"),
        "allowed_edits": _edit_schema(measurements, structure),
        "max_edits_per_proposal": 8,
    }


def _candidate_edits(candidate: dict, context: dict) -> list[dict]:
    if not isinstance(candidate, dict):
        raise _Rejected("invalid_candidate")
    for field, maximum in (("title", 120), ("intent", 600)):
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise _Rejected("invalid_description")
    codes = candidate.get("risk_codes")
    known_codes = {r[key] for r in context["risks"] for key in ("risk_code", "metric_code")}
    if (not isinstance(codes, list) or len(codes) > 20
            or any(not isinstance(code, str) or code not in known_codes for code in codes)):
        raise _Rejected("unknown_risk")
    edits = candidate.get("edits")
    if not isinstance(edits, list) or not 1 <= len(edits) <= 8:
        raise _Rejected("invalid_edit")
    for edit in edits:
        if not isinstance(edit, dict):
            raise _Rejected("invalid_edit")
        op = edit.get("op")
        if not isinstance(op, str) or op not in context["allowed_edits"]:
            raise _Rejected("unsupported_operation")
        schema = context["allowed_edits"][op]
        if "target" in schema["properties"] and edit.get("target") not in schema["properties"]["target"]["enum"]:
            raise _Rejected("unknown_target")
        _validate(edit, schema)
        if op == "add" and (edit["center"][2] < edit["size"][2] / 2
                            or edit["center"][2] + edit["size"][2] / 2 > context["room"]["height_m"]):
            raise _Rejected("invalid_number")
    return deepcopy(edits)


def _improvements(before: dict, after: dict) -> list[dict]:
    old_risks = {r["metric_code"]: r for r in before["risks"] if r["assessment_status"] == "evaluated"}
    new_risks = {r["metric_code"]: r for r in after["risks"] if r["assessment_status"] == "evaluated"}
    if not old_risks:
        raise _Rejected("not_evaluable")
    if old_risks.keys() - new_risks.keys():
        raise _Rejected("lost_evaluability")
    delta = diff_assessments(before, after)["comparison"]["score_delta"]
    if delta is not None and delta < 0 or any(
            _RISK_ORDER[new_risks[code]["risk_level"]] > _RISK_ORDER[r["risk_level"]]
            for code, r in old_risks.items()):
        raise _Rejected("risk_worsened")
    improvements = []
    for code, old in old_risks.items():
        new = new_risks[code]
        a, b = old["measured_value"], new["measured_value"]
        direction = (old.get("threshold") or {}).get("direction")
        improved = _RISK_ORDER[new["risk_level"]] < _RISK_ORDER[old["risk_level"]]
        if type(a) in (int, float) and type(b) in (int, float) and math.isfinite(a) and math.isfinite(b):
            improved |= direction == "below" and b > a + 1e-6 or direction == "above" and b < a - 1e-6
        # Existing low-risk metrics improving cannot by themselves justify a recommendation.
        if improved and old["risk_level"] != "low":
            improvements.append({"metric_code": code, "before": a, "after": b, "unit": old["unit"],
                                 "risk_before": old["risk_level"], "risk_after": new["risk_level"]})
    if not improvements:
        raise _Rejected("no_improvement")
    return improvements


def _require_effective_edits(measurements: dict, structure: dict, edits: list[dict]) -> None:
    previous = {"measurements": measurements, "structure": structure}
    # Evaluate prefixes so assumed IDs have precisely the same numbering as
    # build_scheme. Each action must change state; a no-op cannot invent a strategy.
    for end in range(1, len(edits) + 1):
        state = apply_edits(measurements, structure, edits[:end])
        if state["conflicts"]:
            raise _Rejected("geometry_conflict")
        if all(state[key] == previous[key] for key in ("measurements", "structure")):
            raise _Rejected("no_effect")
        previous = state


def _footprint(box: dict, *, relative=False) -> list[tuple[float, float]]:
    center = [0, 0] if relative else box["center"]
    return _corners(center, box["size"][0], box["size"][1], box["rotation_z_deg"])


def _relative_shape(box: dict) -> tuple:
    return tuple(sorted((round(x, 6), round(y, 6)) for x, y in _footprint(box, relative=True)))


def _require_structured_clearance(before: dict, after: dict) -> None:
    old_objects = {item["id"]: item for item in before["objects"] if item["kind"] == "object"}
    for item in after["objects"]:
        old = old_objects.get(item["id"])
        if item["kind"] != "object" or old is not None and all(
                item[key] == old[key] for key in ("center", "size", "rotation_z_deg")):
            continue
        for obstacle in [*after["objects"], *after["walls"]]:
            if obstacle is item:
                continue
            # Boxes which only touch or occupy separate heights cannot collide.
            vertical_overlap = min(item["center"][2] + item["size"][2] / 2,
                                   obstacle["center"][2] + obstacle["size"][2] / 2) - max(
                item["center"][2] - item["size"][2] / 2,
                obstacle["center"][2] - obstacle["size"][2] / 2)
            if vertical_overlap > 1e-6 and overlap_depth(_footprint(item), _footprint(obstacle)) > 1e-6:
                raise _Rejected("geometry_conflict")


def _strategy(before: dict, after: dict, improvements: list[dict]) -> tuple:
    # Derive operations from the final geometry rather than the edit history.
    # Cancelled rotations/moves cannot manufacture a distinct strategy.
    old_objects = {item["id"]: item for item in before["objects"] if item["kind"] == "object"}
    new_objects = {item["id"]: item for item in after["objects"] if item["kind"] == "object"}
    operations = set()
    for target in old_objects.keys() | new_objects.keys():
        old, new = old_objects.get(target), new_objects.get(target)
        if old is None:
            operations.add(("add", "assumed_objects"))
        elif new is None:
            operations.add(("remove", target))
        else:
            if any(not math.isclose(a, b, abs_tol=1e-6, rel_tol=0)
                   for a, b in zip(old["center"], new["center"])):
                operations.add(("move", target))
            if _relative_shape(old) != _relative_shape(new):
                operations.add(("rotate", target))
    if before["doors"] != after["doors"]:
        operations.add(("resize_door", "all_doors"))
    effects = tuple(sorted((item["metric_code"], item["risk_after"]) for item in improvements))
    return tuple(sorted(operations)), effects


def validate_room_candidate(measurements: dict, structure: dict, candidate: dict,
                            *, baseline: dict | None = None) -> tuple[dict, list[dict]]:
    """Verify one candidate for generation or saving, without model access.

    Returns the complete independent build_scheme result and formal improvement
    evidence. A trusted internal caller may reuse its already computed baseline.
    Raises ProposalRejected with a safe reason code for any candidate failure.
    """
    try:
        if baseline is None:
            baseline = build_scheme(measurements, structure, [])
        context = _context(measurements, structure, baseline, None)
        edits = _candidate_edits(candidate, context)
        _require_effective_edits(measurements, structure, edits)
        result = build_scheme(measurements, structure, edits)
        if result["conflicts"] or len(result["applied_edits"]) != len(edits):
            raise ProposalRejected("geometry_conflict")
        if result["measurements"] == measurements and result["structure"] == structure:
            raise ProposalRejected("no_effect")
        _require_structured_clearance(result["layout_before"], result["layout_after"])
        improvements = _improvements(baseline, result)
        json.dumps(result["layout_after"], allow_nan=False)
        return result, improvements
    except ProposalRejected:
        raise
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        raise ProposalRejected("not_evaluable") from None


def generate_room_proposals(measurements: dict, structure: dict, goal: str | None, request_fn: Callable) -> dict:
    """Return 2–3 verified choices, or a reasoned failure after one revision."""
    baseline = build_scheme(measurements, structure, [])
    context = _context(measurements, structure, baseline, goal)
    accepted, rejections = [], []
    strategies, layouts = set(), set()
    for round_number in (1, 2):
        feedback = None if round_number == 1 else [*rejections, {
            "reason": "insufficient_distinct_proposals", "accepted_count": len(accepted),
            "accepted_strategies": [p["edits"] for p in accepted], "required_count": 2}]
        candidates = request_fn(deepcopy(context), deepcopy(feedback))
        if not isinstance(candidates, list) or not candidates:
            rejections.append({"round": round_number, "reason": "empty_candidates"})
            candidates = []
        # Bound the amount of synchronous geometry work even for a valid huge JSON list.
        for index, candidate in enumerate(candidates[:12]):
            try:
                result, improvements = validate_room_candidate(measurements, structure, candidate, baseline=baseline)
                edits = deepcopy(candidate["edits"])
                strategy = _strategy(result["layout_before"], result["layout_after"], improvements)
                layout = json.dumps(result["layout_after"], sort_keys=True, allow_nan=False)
                if strategy in strategies or layout in layouts:
                    raise _Rejected("duplicate_strategy")
                strategies.add(strategy)
                layouts.add(layout)
                diff = diff_assessments(baseline, result)
                construction = any(e["op"] == "resize_door" for e in edits)
                limitations = [*LIMITATIONS]
                if construction:
                    limitations.append("门宽调整仅是假设施工方案；模拟同时调整本房间所有门，须专业人员复核。")
                if result["assumed_ids"]:
                    limitations.append("新增物体尺寸及位置为假设，尚未实测。")
                accepted.append({
                    "title": candidate["title"].strip(), "intent": candidate["intent"].strip(),
                    "risk_codes": list(dict.fromkeys(candidate["risk_codes"])), "edits": edits,
                    **{key: result[key] for key in ("layout_before", "layout_after", "metrics", "risks", "overall", "category_scores", "scope", "applied_edits", "assumed_ids")},
                    **diff, "requires_construction": construction,
                    "evidence": {
                        "facts": {"source": "structured_scan", "score": context["score"], "risks": context["risks"]},
                        "design_intent": {"source": "model", "text": candidate["intent"].strip()},
                        "verification": {"source": "build_scheme", "improvements": improvements, **diff}},
                    "limitations": limitations, "disclaimer": DISCLAIMER,
                })
                if len(accepted) == 3:
                    break
            except _Rejected as exc:
                rejections.append({"round": round_number, "candidate_index": index, "reason": str(exc)})
            except (ValueError, TypeError, KeyError, IndexError, OverflowError):
                rejections.append({"round": round_number, "candidate_index": index, "reason": "not_evaluable"})
        if len(accepted) >= 2:
            return {"status": "ready", "rounds": round_number, "proposals": accepted, "rejections": rejections}
    return {"status": "insufficient", "rounds": 2, "proposals": [], "rejections": rejections,
            "message": "当前资料或房间条件下不足以生成多个可信方案，请补充测量资料或调整目标后重试。"}
