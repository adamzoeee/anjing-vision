"""应用独立的数据级结构复核；不在算法中硬编码某个房间。"""
from __future__ import annotations

import json
import math
from pathlib import Path


def _walls(bounds: dict, height: float, thickness: float = 0.06) -> list[dict]:
    lo, hi = bounds["min"], bounds["max"]
    return [
        {"id": 0, "center": [(lo[0] + hi[0]) / 2, lo[1], height / 2], "size": [hi[0] - lo[0], thickness, height], "rotation_z_deg": 0.0},
        {"id": 1, "center": [hi[0], (lo[1] + hi[1]) / 2, height / 2], "size": [hi[1] - lo[1], thickness, height], "rotation_z_deg": 90.0},
        {"id": 2, "center": [(lo[0] + hi[0]) / 2, hi[1], height / 2], "size": [hi[0] - lo[0], thickness, height], "rotation_z_deg": 0.0},
        {"id": 3, "center": [lo[0], (lo[1] + hi[1]) / 2, height / 2], "size": [hi[1] - lo[1], thickness, height], "rotation_z_deg": 90.0},
    ]


def _set_pose(item: dict, center: list[float], size: list[float], yaw: float,
              status: str) -> None:
    """统一更新实例和 bbox，避免复核数据出现两套互相矛盾的位姿。"""
    center = [float(value) for value in center]
    size = [float(value) for value in size]
    item.update(center=center, size=size, rotation_z_deg=float(yaw))
    item["bbox"] = {"center": center, "size": size, "rotation_z_deg": float(yaw)}
    item["dimensions"] = {"length": size[0], "width": size[1], "height": size[2]}
    item["geometry_status"] = "verified"
    item["measurement_ready"] = True
    item["review_status"] = status


def _apply_video_layout_constraints(structure: dict, review: dict,
                                    by_id: dict[str, dict]) -> None:
    """把视频得到的拓扑关系转换为米制布局。

    约束只描述“靠哪面墙、谁挨着谁、谁是组合家具”，不保存房间专用
    的绝对中心点。这样换房间后仍由当前房间边界和实测尺寸重新求解。
    wall id: 0=-Y, 1=+X, 2=+Y, 3=-X。
    """
    spec = review.get("layout_constraints") or {}
    if not spec:
        return
    bounds = structure["room"]["bounds_xy"]
    lo = [float(v) for v in bounds["min"]]
    hi = [float(v) for v in bounds["max"]]

    # 角落家具：主长度沿 first wall，宽度沿另一面墙；天然同时贴两墙。
    for rule in spec.get("corner_items", []):
        item = by_id.get(str(rule.get("instance_id")))
        if item is None:
            continue
        size = [float(v) for v in rule.get("size", item.get("size", [1, 1, 1]))]
        corner = str(rule.get("corner", "+x,+y"))
        long_axis = str(rule.get("long_axis", "y"))
        yaw = 90.0 if long_axis == "y" else 0.0
        x_extent = size[1] if yaw == 90.0 else size[0]
        y_extent = size[0] if yaw == 90.0 else size[1]
        cx = hi[0] - x_extent / 2 if "+x" in corner else lo[0] + x_extent / 2
        cy = hi[1] - y_extent / 2 if "+y" in corner else lo[1] + y_extent / 2
        center_z = float(rule.get("center_z", size[2] / 2))
        _set_pose(item, [cx, cy, center_z], size, yaw, "video_corner_two_wall_constraint")

    # 沿墙连续排布。anchor 可引用已放好的家具，保证相邻但不重叠。
    for rule in spec.get("wall_items", []):
        item = by_id.get(str(rule.get("instance_id")))
        if item is None:
            continue
        size = [float(v) for v in rule.get("size", item.get("size", [1, 1, 1]))]
        wall_id = int(rule["wall_id"])
        yaw = 0.0 if wall_id in (0, 2) else 90.0
        x_extent = size[1] if yaw == 90.0 else size[0]
        y_extent = size[0] if yaw == 90.0 else size[1]
        anchor = by_id.get(str(rule.get("adjacent_to")))
        direction = float(rule.get("direction", 1.0))
        gap = float(rule.get("gap", 0.0))
        if wall_id in (0, 2):
            cy = lo[1] + y_extent / 2 if wall_id == 0 else hi[1] - y_extent / 2
            if anchor:
                ayaw = math.radians(float(anchor.get("rotation_z_deg", 0.0)))
                asize = anchor.get("size", [1, 1, 1])
                ax_extent = abs(math.cos(ayaw)) * asize[0] + abs(math.sin(ayaw)) * asize[1]
                cx = float(anchor["center"][0]) + direction * (ax_extent + x_extent) / 2 + direction * gap
            else:
                offset = float(rule.get("offset", 0.0))
                cx = (lo[0] + x_extent / 2 + offset) if direction > 0 else (hi[0] - x_extent / 2 - offset)
        else:
            cx = lo[0] + x_extent / 2 if wall_id == 3 else hi[0] - x_extent / 2
            if anchor:
                ayaw = math.radians(float(anchor.get("rotation_z_deg", 0.0)))
                asize = anchor.get("size", [1, 1, 1])
                ay_extent = abs(math.sin(ayaw)) * asize[0] + abs(math.cos(ayaw)) * asize[1]
                cy = float(anchor["center"][1]) + direction * (ay_extent + y_extent) / 2 + direction * gap
            else:
                offset = float(rule.get("offset", 0.0))
                cy = (lo[1] + y_extent / 2 + offset) if direction > 0 else (hi[1] - y_extent / 2 - offset)
        center_z = float(rule.get("center_z", size[2] / 2))
        _set_pose(item, [cx, cy, center_z], size, yaw, "video_wall_attachment_constraint")

    # 组合家具（如书桌上方的一体式书柜）共享 XY，但在 Z 上接续，
    # 不再被误当成两件互相穿插的落地障碍物。
    for rule in spec.get("mounted_items", []):
        item = by_id.get(str(rule.get("instance_id")))
        parent = by_id.get(str(rule.get("parent_id")))
        if item is None or parent is None:
            continue
        size = [float(v) for v in rule.get("size", item.get("size", [1, 1, 1]))]
        along = float(rule.get("along_offset", 0.0))
        yaw = float(parent.get("rotation_z_deg", 0.0))
        theta = math.radians(yaw)
        center = [
            float(parent["center"][0]) + along * math.cos(theta),
            float(parent["center"][1]) + along * math.sin(theta),
            float(parent["center"][2]) + float(parent["size"][2]) / 2 + size[2] / 2,
        ]
        _set_pose(item, center, size, yaw, "video_composite_furniture_constraint")
        item["attached_to"] = str(rule["parent_id"])
        item["floor_obstacle"] = False


def apply_structure_review(structure_json: Path, review_json: Path) -> dict:
    structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
    review = json.loads(Path(review_json).read_text(encoding="utf-8"))
    if review.get("coordinate_unit") != "scene_units":
        raise ValueError("structure review must explicitly use scene_units")
    room = review.get("room") or {}
    if room:
        bounds = room["bounds_xy"]
        height = float(room["height"])
        structure["room"].update(
            bounds_xy=bounds, height_m=height,
            floor_polygon=[
                [bounds["min"][0], bounds["min"][1], 0.0],
                [bounds["max"][0], bounds["min"][1], 0.0],
                [bounds["max"][0], bounds["max"][1], 0.0],
                [bounds["min"][0], bounds["max"][1], 0.0],
            ],
        )
        structure["walls"] = _walls(bounds, height)
    for collection in ("doors", "windows"):
        if collection in review:
            structure[collection] = review[collection]
    by_id = {str(item.get("instance_id")): item for item in structure.get("semantic_instances", [])}
    for patch in review.get("instance_overrides", []):
        instance_id = str(patch["instance_id"])
        if instance_id not in by_id:
            continue
        by_id[instance_id].update({key: value for key, value in patch.items() if key != "instance_id"})
    for instance_id in review.get("drop_instances", []):
        by_id.pop(str(instance_id), None)
    for item in review.get("additional_instances", []):
        by_id[str(item["instance_id"])] = item
    _apply_video_layout_constraints(structure, review, by_id)
    include_ids = {str(value) for value in review.get("include_instances", [])}
    if include_ids:
        by_id = {key: value for key, value in by_id.items() if key in include_ids}
    structure["semantic_instances"] = list(by_id.values())
    if "geometric_obstacles" in review:
        structure["geometric_obstacles"] = review["geometric_obstacles"]
    layout_source = str(review.get("layout_source") or "independent_review")
    structure["layout_source"] = layout_source
    if "video_topology" in review:
        structure["video_topology"] = review["video_topology"]
    structure["independent_review"] = {
        "status": "applied", "source": Path(review_json).name,
        "evidence": review.get("evidence", []),
        "layout_source": layout_source,
        "note": "框架布局以多视角视频为主；点云只用于坐标、尺度和一致性校验",
    }
    structure.setdefault("counts", {}).update(
        walls=len(structure.get("walls", [])), doors=len(structure.get("doors", [])),
        windows=len(structure.get("windows", [])),
    )
    Path(structure_json).write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
    return structure
