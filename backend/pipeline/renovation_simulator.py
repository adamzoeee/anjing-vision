"""改造模拟：读取真实家具几何 → 内存副本执行修改 → 重算空间指标 → 正式评分体系。

不再使用“把指标抬到安全阈值”的假模拟。所有指标由 renovation_geometry
从盒子几何重新计算，评分由 pipeline.risk_assessment 正式规则给出。
"""
from __future__ import annotations

import math
from copy import deepcopy

import numpy as np

from pipeline.renovation_compare import compare_assessments
from pipeline.renovation_geometry import (
    apply_ops, build_grid, compute_metrics, load_geometry, nearest_box,
    payload_from_geometry, _corridor_width,
)
from pipeline.risk_assessment import build_risk_assessment
from pipeline.spatial_metrics import METRIC_DEFINITION_BY_CODE

# 建议(风险 metric_code) → 具体几何操作
_SUGGESTION_OPS = {
    "door_width": "widen_door",
    "main_passage_width": "clear_corridor",
    "minimum_passage_width": "clear_corridor",
    "entrance_space": "clear_entrance",
    "furniture_spacing": "separate_closest",
    "bedside_clearance": "clear_bedside",
    "bed_surrounding_space": "clear_bedside",
    "path_obstruction": "remove_path_boxes",
    "path_continuity": "remove_path_boxes",
    "activity_area": "clear_activity",
    "main_activity_area_safety": "clear_activity",
    "crowding": "remove_largest",
    "wall_furniture_clearance": "pull_off_wall",
    "bed_wall_distance": "pull_bed_off_wall",
    # path_length 无法用简单几何操作精确对应 → 定性
}

_QUALITATIVE = {"path_length"}


def _find_box(geo: dict, target: str):
    for collection in ("furniture", "obstacles"):
        for box in geo[collection]:
            if box["label"] == target or box["id"] == target:
                return box
    # 模糊：标签包含
    for collection in ("furniture", "obstacles"):
        for box in geo[collection]:
            if target in str(box["label"]):
                return box
    return None


def _corridor_axis(geo: dict):
    door = geo["door"]
    bed = next((b for b in geo["furniture"] if b["label"] in {"bed", "床"}), None)
    if door is None or bed is None:
        return None
    return np.asarray(door["center"][:2]), np.asarray(bed["center"][:2])


def _wall_blocked(geo: dict, box: dict, direction) -> bool:
    """沿 direction 移动时盒子是否贴墙且正朝墙移动（此时移动无效，只能移除）。"""
    for axis in (0, 1):
        comp = float(direction[axis])
        if comp > 1e-9 and box["center"][axis] + box["size"][axis] / 2 >= geo["hi"][axis] - 1e-6:
            return True
        if comp < -1e-9 and box["center"][axis] - box["size"][axis] / 2 <= geo["lo"][axis] + 1e-6:
            return True
    return False


def _nearest_non_bed(geo: dict, xy, narrow_of=None):
    cands = [b for b in [*geo["furniture"], *geo["obstacles"]]
             if b["kind"] != "wall" and b["label"] not in {"bed", "床"}]
    if not cands:
        return None
    if narrow_of is not None:
        # 排除最窄点本身所在的盒子
        cands = [b for b in cands if b is not narrow_of]
        if not cands:
            return None
    return min(cands, key=lambda b: math.dist(b["center"][:2], xy))


def _away_op(geo: dict, box: dict, away_from, distance: float) -> dict:
    away = np.asarray(box["center"][:2]) - np.asarray(away_from)
    norm = float(np.linalg.norm(away)) or 1.0
    direction = away / norm
    if _wall_blocked(geo, box, direction):
        return {"op": "remove", "box": box}
    return {"op": "move_away", "box": box, "away_from": list(away_from),
            "distance": distance, "clamp": True}


def _suggestion_op(geo: dict, metric_code: str) -> list[dict] | None:
    if metric_code in _QUALITATIVE:
        return None
    door = geo["door"]
    bed = next((b for b in geo["furniture"] if b["label"] in {"bed", "床"}), None)
    if metric_code == "widen_door" or metric_code == "door_width":
        if door is None or float(door["size"][0]) >= 0.90:
            return None
        return [{"op": "widen_door"}]
    if metric_code in {"main_passage_width", "minimum_passage_width"}:
        axis = _corridor_axis(geo)
        if axis is None:
            return None
        start, goal = axis
        grid, origin, cell = build_grid(geo)
        _, narrow = _corridor_width(grid, origin, start, goal)
        if narrow is None:
            return None
        box = _nearest_non_bed(geo, narrow[:2])
        if box is None:
            return None
        return [_away_op(geo, box, narrow[:2], 0.50)]
    if metric_code == "entrance_space":
        if door is None:
            return None
        box = nearest_box(geo, np.asarray(door["center"][:2]), exclude_kind="wall")
        if box is None:
            return None
        return [_away_op(geo, box, np.asarray(door["center"][:2]), 0.50)]
    if metric_code in {"bedside_clearance", "bed_surrounding_space"}:
        if bed is None:
            return None
        box = _nearest_non_bed(geo, bed["center"][:2])
        if box is None:
            return None
        return [_away_op(geo, box, bed["center"][:2], 0.40)]
    if metric_code == "furniture_spacing":
        pairs = []
        for i in range(len(geo["furniture"])):
            for j in range(i + 1, len(geo["furniture"])):
                pairs.append((float(np.linalg.norm(
                    geo["furniture"][i]["center"][:2] - geo["furniture"][j]["center"][:2])),
                    geo["furniture"][i], geo["furniture"][j]))
        if not pairs:
            return None
        _, a, b = min(pairs)
        return [_away_op(geo, b, a["center"][:2], 0.35)]
    if metric_code in {"path_obstruction", "path_continuity"}:
        boxes = [b for b in geo["obstacles"] if b["label"] in {"box", "obstacle"}]
        if not boxes:
            return None
        return [{"op": "remove", "box": b} for b in boxes]
    if metric_code in {"activity_area", "main_activity_area_safety"}:
        cx = (geo["lo"][0] + geo["hi"][0]) / 2
        cy = (geo["lo"][1] + geo["hi"][1]) / 2
        box = nearest_box(geo, (cx, cy), exclude_kind="wall")
        if box is None:
            return None
        return [{"op": "remove", "box": box}]
    if metric_code == "crowding":
        cands = [b for b in geo["furniture"] if b is not bed]
        if not cands:
            return None
        biggest = max(cands, key=lambda b: b["size"][0] * b["size"][1])
        return [{"op": "remove", "box": biggest}]
    if metric_code == "wall_furniture_clearance":
        box = nearest_box(geo, ((geo["lo"][0] + geo["hi"][0]) / 2,
                                (geo["lo"][1] + geo["hi"][1]) / 2), exclude_kind="wall")
        if box is None:
            return None
        cx = (geo["lo"][0] + geo["hi"][0]) / 2
        cy = (geo["lo"][1] + geo["hi"][1]) / 2
        return [_away_op(geo, box, (cx, cy), 0.15)]
    if metric_code == "bed_wall_distance":
        if bed is None:
            return None
        cx = (geo["lo"][0] + geo["hi"][0]) / 2
        cy = (geo["lo"][1] + geo["hi"][1]) / 2
        return [_away_op(geo, bed, (cx, cy), 0.20)]
    return None


def _add_position(geo: dict, intent: dict, size: list[float]) -> list[float]:
    """把“床边/门口/书桌旁”等自然语言位置转成世界坐标：贴着锚点物外侧放。"""
    text = intent.get("position") or intent.get("raw") or ""
    target = intent.get("target")
    anchor = _find_box(geo, target) if target else None
    door = geo["door"]
    is_door = anchor is None and door is not None and "门" in text
    if anchor is None:
        if is_door:
            anchor_xy = np.asarray(door["center"][:2], dtype=float)
            anchor_size = np.asarray(door["size"][:2], dtype=float)
        else:
            bed = next((b for b in geo["furniture"] if b["label"] in {"bed", "床"}), None)
            if bed is None:
                anchor_xy = np.asarray([(geo["lo"][0] + geo["hi"][0]) / 2,
                                        (geo["lo"][1] + geo["hi"][1]) / 2], dtype=float)
                anchor_size = np.zeros(2)
            else:
                anchor_xy = bed["center"][:2].copy()
                anchor_size = bed["size"][:2].copy()
    else:
        anchor_xy = anchor["center"][:2].copy()
        anchor_size = anchor["size"][:2].copy()
    direction = None
    for word, vec in (("左", (-1.0, 0.0)), ("右", (1.0, 0.0)),
                      ("前", (0.0, 1.0)), ("上", (0.0, 1.0)),
                      ("后", (0.0, -1.0)), ("下", (0.0, -1.0))):
        if word in text:
            direction = np.asarray(vec, dtype=float)
            break
    if direction is None:
        # 默认放在锚点朝向房间中心的一侧（对门来说就是室内侧）
        room_c = np.asarray([(geo["lo"][0] + geo["hi"][0]) / 2,
                             (geo["lo"][1] + geo["hi"][1]) / 2], dtype=float)
        to_room = room_c - anchor_xy
        if float(np.linalg.norm(to_room)) < 1e-6:
            to_room = np.array([1.0, 0.0])
        dominant = 1 if abs(to_room[1]) >= abs(to_room[0]) else 0
        direction = np.zeros(2)
        direction[dominant] = np.sign(to_room[dominant]) or 1.0
    margin = 0.05
    box_half = np.asarray([size[0] / 2, size[1] / 2], dtype=float)
    anchor_half = anchor_size[:2] / 2 if anchor_size.size else np.zeros(2)
    offset = direction * (anchor_half * np.abs(direction) + box_half * np.abs(direction) + margin)
    center = anchor_xy + offset
    # 夹在房间边界内
    center[0] = min(max(center[0], geo["lo"][0] + box_half[0]), geo["hi"][0] - box_half[0])
    center[1] = min(max(center[1], geo["lo"][1] + box_half[1]), geo["hi"][1] - box_half[1])
    return [float(center[0]), float(center[1]), float(size[2] / 2)]


def simulate(metric_payload: dict, intent: dict, structure: dict,
             measurements: dict | None = None) -> dict:
    """在内存副本上执行意图，重算指标与正式评分；无几何对应时诚实标记定性。

    当前分：优先使用正式指标载荷（与报告页一致）的分数；
    修改后分：正式当前分 + 几何重算得出的分数变化（Δ 完全来自真实几何，
    不含任何“抬到安全阈值”的假模拟）。
    """
    geo = load_geometry(structure, measurements)
    geometry_before_payload = payload_from_geometry(geo)
    geometry_before = build_risk_assessment(geometry_before_payload)

    # 正式当前分与建议排序：仅当传入载荷包含完整正式指标时采用
    official_assessment = None
    official_before_score = None
    try:
        official_metrics = (metric_payload or {}).get("metrics") or []
        codes = {m.get("metric_code") for m in official_metrics}
        if codes == set(METRIC_DEFINITION_BY_CODE):
            official_assessment = build_risk_assessment(metric_payload)
            official_before_score = (official_assessment.get("overall") or {}).get("score")
    except Exception:
        official_assessment = None
        official_before_score = None
    suggestion_source = official_assessment or geometry_before

    def finalize_comparison(before_assessment, after_assessment):
        comparison = compare_assessments(before_assessment, after_assessment)
        delta = comparison.get("score_delta")
        if official_before_score is not None:
            comparison["before_score"] = official_before_score
            comparison["after_score"] = (
                round(official_before_score + delta, 1) if delta is not None else None
            )
        return comparison

    action = intent["action"]
    ops: list[dict] = []
    qualitative = False
    if action == "APPLY_SUGGESTIONS":
        # 由建议对应的风险 metric_code 找几何操作
        ids = intent["suggestion_ids"]
        if ids == ["all"]:
            ids = [i + 1 for i in range(len(suggestion_source.get("top_risks", [])))]
        applied = []
        for index in ids:
            if index < 1 or index > len(suggestion_source.get("top_risks", [])):
                raise ValueError(f"建议{index}不存在")
            risk = suggestion_source["top_risks"][index - 1]
            code = risk["metric_code"]
            sub_ops = _suggestion_op(geo, code)
            if sub_ops:
                ops.extend(sub_ops)
                applied.append({"id": index, "name": risk["risk_name"],
                                "metric_code": code, "ops": len(sub_ops)})
            else:
                applied.append({"id": index, "name": risk["risk_name"],
                                "metric_code": code, "qualitative": True})
        if not ops:
            qualitative = True
        intent["applied_suggestions"] = applied
    elif action == "MOVE":
        box = _find_box(geo, intent["target"])
        if box is None:
            raise ValueError(f"没有找到要移动的家具：{intent['target']}")
        if intent.get("toward_wall"):
            cx = (geo["lo"][0] + geo["hi"][0]) / 2
            cy = (geo["lo"][1] + geo["hi"][1]) / 2
            away = np.asarray(box["center"][:2]) - np.asarray([cx, cy])
            norm = float(np.linalg.norm(away)) or 1.0
            ops.append({"op": "move", "box": box, "dx": away[0] / norm * (intent["distance_m"] or 0.3),
                        "dy": away[1] / norm * (intent["distance_m"] or 0.3)})
        elif intent.get("toward_door"):
            door = geo["door"]
            if door is None:
                qualitative = True
            else:
                away = np.asarray(door["center"][:2]) - box["center"][:2]
                norm = float(np.linalg.norm(away)) or 1.0
                ops.append({"op": "move", "box": box, "dx": away[0] / norm * (intent["distance_m"] or 0.3),
                            "dy": away[1] / norm * (intent["distance_m"] or 0.3)})
        else:
            ops.append({"op": "move", "box": box,
                        "dx": intent["dx"] * intent["distance_m"],
                        "dy": intent["dy"] * intent["distance_m"]})
    elif action == "ADD":
        size = intent["size_m"]
        center = _add_position(geo, intent, size)
        ops.append({"op": "add", "center": center, "size": size})
    elif action == "REMOVE":
        box = _find_box(geo, intent["target"])
        if box is None:
            raise ValueError(f"没有找到要移除的物体：{intent['target']}")
        if box["kind"] == "wall":
            raise ValueError("墙体不可移除")
        ops.append({"op": "remove", "box": box})

    if not ops:
        return {
            "before": geometry_before, "after": geometry_before,
            "comparison": finalize_comparison(geometry_before, geometry_before),
            "metric_changes": [], "qualitative": True,
            "message": "该建议当前只能提供定性建议，暂时无法精确计算评分变化。",
        }

    simulated_geo = apply_ops(geo, ops)
    after_payload = payload_from_geometry(simulated_geo)
    after = build_risk_assessment(after_payload)
    comparison = finalize_comparison(geometry_before, after)

    # 关键指标变化
    before_metrics = {m["metric_code"]: m for m in geometry_before_payload.get("metrics", [])}
    after_metrics = {m["metric_code"]: m for m in after_payload.get("metrics", [])}
    changes = []
    for code in before_metrics:
        if code not in after_metrics:
            continue
        a, b = before_metrics[code], after_metrics[code]
        if a.get("value") != b.get("value") and a.get("status") != "not_evaluable":
            changes.append({"metric_code": code, "name": a["name"], "unit": a["unit"],
                            "before": a.get("value"), "after": b.get("value")})
    # 风险变化
    risk_before = {r["metric_code"]: r.get("risk_level") for r in geometry_before.get("risks", [])}
    risk_changes = []
    for r in after.get("risks", []):
        old = risk_before.get(r["metric_code"])
        if old is not None and r.get("risk_level") != old:
            risk_changes.append({"name": r["risk_name"], "before": old, "after": r.get("risk_level")})
    return {
        "before": geometry_before, "after": after, "comparison": comparison,
        "metric_changes": changes, "risk_changes": risk_changes,
        "qualitative": qualitative,
        "message": None,
    }
