"""自然语言改造指令 → 沙盒 edits（只生成编辑，不出分数）。"""
from __future__ import annotations

from pipeline.scheme_state import object_id

_CN_ALIAS = {
    "床": "bed", "书桌": "desk", "桌子": "desk", "桌": "desk", "书架": "bookshelf",
    "柜子": "cabinet", "衣柜": "wardrobe", "沙发": "sofa", "椅子": "chair",
    "凳子": "stool", "箱子": "box", "置物架": "storage_rack", "电视柜": "tv_stand",
}


def resolve_target(measurements: dict, structure: dict, name: str) -> str | None:
    """按 instance_id / 英文类型 / 中文别名匹配家具或开口。"""
    if not name:
        return None
    wanted = _CN_ALIAS.get(name, name)
    objects = list(measurements.get("objects", []))
    for item in objects:
        if object_id(item) == name or str(item.get("type") or "") == wanted:
            return object_id(item)
    for item in objects:
        if wanted in str(item.get("type") or ""):
            return object_id(item)
    for item in structure.get("semantic_instances", []):
        if object_id(item) == name or wanted in str(item.get("label") or ""):
            return object_id(item)
    for opening in measurements.get("openings", []):
        if object_id(opening) == name or wanted in str(opening.get("type") or ""):
            return object_id(opening)
    return None


def _anchor_center(intent: dict, measurements: dict, structure: dict, size: list[float]) -> list[float]:
    bounds = (structure.get("room") or {}).get("bounds_xy") or {}
    lo = bounds.get("min") or [0.0, 0.0]
    hi = bounds.get("max") or [4.0, 4.0]
    anchor = None
    target = resolve_target(measurements, structure, str(intent.get("target") or ""))
    if target:
        anchor = next((o for o in measurements.get("objects", []) if object_id(o) == target), None)
    if anchor is None and "门" in str(intent.get("position") or intent.get("raw") or ""):
        anchor = next((o for o in measurements.get("openings", []) if o.get("type") == "door"), None)
    if anchor is None:
        return [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, size[2] / 2]
    ax, ay = float(anchor["center"][0]), float(anchor["center"][1])
    half = float(anchor.get("length_m") or anchor.get("width_m") or 0.5) / 2
    x = ax + half + size[0] / 2 + 0.05
    x = min(max(x, lo[0] + size[0] / 2), hi[0] - size[0] / 2)
    y = min(max(ay, lo[1] + size[1] / 2), hi[1] - size[1] / 2)
    return [x, y, size[2] / 2]


def intent_to_edits(intent: dict, measurements: dict, structure: dict) -> list[dict]:
    """把 parse_intent 的结果转成 edits；找不到目标时抛 ValueError（由路由转 422）。"""
    action = intent.get("action")
    if action == "MOVE":
        target = resolve_target(measurements, structure, str(intent.get("target") or ""))
        if target is None:
            raise ValueError(f"没有找到要移动的家具：{intent.get('target')}")
        distance = float(intent.get("distance_m") or 0.3)
        return [{"op": "move", "target": target,
                 "dx": float(intent.get("dx") or 0.0) * distance,
                 "dy": float(intent.get("dy") or 0.0) * distance}]
    if action == "REMOVE":
        target = resolve_target(measurements, structure, str(intent.get("target") or ""))
        if target is None:
            raise ValueError(f"没有找到要移除的物体：{intent.get('target')}")
        return [{"op": "remove", "target": target}]
    if action == "ADD":
        size = [float(v) for v in intent.get("size_m") or [0.4, 0.4, 0.4]]
        center = _anchor_center(intent, measurements, structure, size)
        return [{"op": "add", "label": intent.get("label") or "箱子", "size": size, "center": center}]
    raise ValueError(f"暂不支持的改造指令：{action}")