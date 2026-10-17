"""改造方案的派生状态：在内存副本上应用 edits，绝不触碰真实产物。

约束：
- 只处理 measurements.objects / measurements.openings / structure.semantic_instances（兼容 structure.objects）；
- 越界、与其他家具重叠的编辑一律拒绝并记入 conflicts；
- 新增物体的 measurement_status 恒为 "assumed"，由 space_foundation 显式接受。
"""
from __future__ import annotations

import math
from copy import deepcopy

ASSUMED_STATUS = "assumed"
_EDGE_TOL = 1e-6


def object_id(item: dict) -> str:
    return str(item.get("instance_id") or item.get("id") or "")


def _corners(center, length, width, rotation_deg) -> list[tuple[float, float]]:
    t = math.radians(float(rotation_deg or 0.0))
    c, s = math.cos(t), math.sin(t)
    hx, hy = float(length) / 2.0, float(width) / 2.0
    return [
        (center[0] + sx * hx * c - sy * hy * s, center[1] + sx * hx * s + sy * hy * c)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]


def _rect_of_measurement(item: dict) -> list[tuple[float, float]]:
    return _corners(item["center"], item.get("length_m") or 0.0,
                    item.get("width_m") or 0.0, item.get("rotation_z_deg") or 0.0)


def _axes(corners):
    axes = []
    for i, (x1, y1) in enumerate(corners):
        x2, y2 = corners[(i + 1) % len(corners)]
        nx, ny = -(y2 - y1), (x2 - x1)
        norm = math.hypot(nx, ny) or 1.0
        axes.append((nx / norm, ny / norm))
    return axes


def _project(corners, axis) -> tuple[float, float]:
    values = [x * axis[0] + y * axis[1] for x, y in corners]
    return min(values), max(values)


def overlap_depth(a_corners, b_corners) -> float:
    """两个矩形的重叠深度（米）；不相交返回 0.0（SAT）。"""
    if not a_corners or not b_corners:
        return 0.0
    best = float("inf")
    for axis in _axes(a_corners) + _axes(b_corners):
        a_min, a_max = _project(a_corners, axis)
        b_min, b_max = _project(b_corners, axis)
        overlap = min(a_max, b_max) - max(a_min, b_min)
        if overlap <= _EDGE_TOL:
            return 0.0
        best = min(best, overlap)
    return float(best)


def _inside_room(corners, bounds: dict) -> bool:
    lo = (bounds or {}).get("min")
    hi = (bounds or {}).get("max")
    if not lo or not hi:
        return True
    return all(lo[0] - _EDGE_TOL <= x <= hi[0] + _EDGE_TOL
               and lo[1] - _EDGE_TOL <= y <= hi[1] + _EDGE_TOL for x, y in corners)


def _find(container: list[dict], target: str) -> dict | None:
    for item in container:
        if object_id(item) == target:
            return item
    return None


def _active_objects(structure: dict) -> list[dict]:
    """Use the same furniture collection as the derived layout."""
    if structure.get("semantic_instances") or "objects" not in structure:
        return structure.setdefault("semantic_instances", [])
    return structure["objects"]


def _wall_local(point, wall: dict) -> tuple[float, float]:
    angle = math.radians(float(wall.get("rotation_z_deg") or 0.0))
    dx, dy = point[0] - wall["center"][0], point[1] - wall["center"][1]
    return dx * math.cos(angle) + dy * math.sin(angle), -dx * math.sin(angle) + dy * math.cos(angle)


def _door_geometry_conflict(door: dict, width: float, walls: list[dict], bounds: dict,
                            wall_id=None) -> str | None:
    """Check the opening segment, excluding visual box thickness across a wall."""
    try:
        center = [float(value) for value in door["center"][:2]]
        angle = math.radians(float(door.get("rotation_z_deg") or 0.0))
        if len(center) != 2 or not all(math.isfinite(value) for value in [*center, angle]):
            return "invalid_door_geometry"
        endpoints = [(center[0] + sign * width / 2 * math.cos(angle),
                      center[1] + sign * width / 2 * math.sin(angle)) for sign in (-1, 1)]
        if not _inside_room(endpoints, bounds):
            return "door_out_of_room"
        linked = [wall for index, wall in enumerate(walls)
                  if wall_id is not None and str(wall.get("id", index)) == str(wall_id)]
        containing = linked
        if not containing:
            containing = []
            for wall in walls:
                along, normal = _wall_local(center, wall)
                parallel = abs(math.sin(angle - math.radians(float(wall.get("rotation_z_deg") or 0.0)))) <= 0.01
                if (parallel and abs(along) <= wall["size"][0] / 2 + _EDGE_TOL
                        and abs(normal) <= wall["size"][1] / 2 + _EDGE_TOL):
                    containing.append(wall)
        if walls and not containing:
            return "door_out_of_wall"
        for wall in containing:
            for point in endpoints:
                along, normal = _wall_local(point, wall)
                if (abs(along) > wall["size"][0] / 2 + _EDGE_TOL
                        or abs(normal) > wall["size"][1] / 2 + _EDGE_TOL):
                    return "door_out_of_wall"
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return "invalid_door_geometry"
    return None


def apply_edits(measurements: dict, structure: dict, edits: list[dict]) -> dict:
    """返回改后的 measurements / structure，以及冲突与被采纳的编辑。"""
    m, s = deepcopy(measurements), deepcopy(structure)
    conflicts: list[dict] = []
    applied: list[dict] = []
    assumed_ids: list[str] = []
    bounds = (s.get("room") or {}).get("bounds_xy") or {}

    for edit in edits or []:
        op = edit.get("op")
        if op == "resize_door":
            raw_width = edit.get("width_m")
            try:
                width = 0.90 if raw_width is None else float(raw_width)
            except (TypeError, ValueError):
                conflicts.append({"type": "invalid_door_width", "op": "resize_door",
                                  "width_m": raw_width})
                continue
            if not 0.5 <= width <= 1.5:
                conflicts.append({"type": "invalid_door_width", "op": "resize_door",
                                  "width_m": width})
                continue
            measured_doors = [opening for opening in m.get("openings", []) if opening.get("type") == "door"]
            structure_doors = [door for door in s.get("doors", []) if door.get("size")]
            invalid = None
            # A resize affects every door: preflight both representations before
            # mutating any of them, so one invalid opening rejects the whole edit.
            for doors, mirrors in ((measured_doors, structure_doors), (structure_doors, measured_doors)):
                for door in doors:
                    mirror = _find(mirrors, object_id(door)) if object_id(door) else None
                    if mirror is None:
                        mirror = next((other for other in mirrors if door.get("center") and other.get("center")
                                       and all(math.isclose(a, b, abs_tol=_EDGE_TOL, rel_tol=0)
                                               for a, b in zip(door["center"][:2], other["center"][:2]))), None)
                    # Generated measurements may omit opening rotation and the
                    # structure may omit IDs. Match by center and inherit only
                    # absent geometry fields, then check both representations.
                    geometry = {**(mirror or {}), **door}
                    reason = _door_geometry_conflict(geometry, width, s.get("walls", []), bounds, geometry.get("wall_id"))
                    if reason:
                        invalid = {"type": reason, "target": object_id(door), "op": "resize_door"}
                        break
                if invalid:
                    break
            if invalid:
                conflicts.append(invalid)
                continue
            for opening in m.get("openings", []):
                if opening.get("type") == "door":
                    opening["width_m"] = width
            for door in s.get("doors", []):
                if door.get("size"):
                    door["size"][0] = width
            applied.append(dict(edit))
            continue

        target = str(edit.get("target") or "")
        if op == "add":
            size = [float(v) for v in edit.get("size") or [0.4, 0.4, 0.4]]
            center = [float(v) for v in edit.get("center") or [0.0, 0.0, size[2] / 2]]
            oid = f"assumed_{len(assumed_ids) + 1}"
            candidate = {"center": center, "length_m": size[0], "width_m": size[1],
                         "rotation_z_deg": float(edit.get("rotation_z_deg") or 0.0)}
            corners = _rect_of_measurement(candidate)
            if not _inside_room(corners, bounds):
                conflicts.append({"type": "out_of_room", "target": oid, "op": "add"})
                continue
            other = next(({"id": object_id(o), "depth": overlap_depth(corners, _rect_of_measurement(o))}
                          for o in m.get("objects", []) if overlap_depth(corners, _rect_of_measurement(o)) > _EDGE_TOL), None)
            if other:
                conflicts.append({"type": "overlap", "target": oid, "other": other["id"],
                                  "overlap_m": round(other["depth"], 3), "op": "add"})
                continue
            label = edit.get("label") or "object"
            m.setdefault("objects", []).append({
                "id": oid, "instance_id": oid, "type": label, "center": center,
                "length_m": size[0], "width_m": size[1], "height_m": size[2],
                "rotation_z_deg": candidate["rotation_z_deg"],
                "measurement_status": ASSUMED_STATUS, "measurement_reason": "user_assumption",
                "confidence": None,
            })
            _active_objects(s).append({
                "instance_id": oid, "label": label, "center": center, "size": list(size),
                "rotation_z_deg": candidate["rotation_z_deg"], "assumed": True,
            })
            assumed_ids.append(oid)
            applied.append({**edit, "target": oid})
            continue

        item = _find(m.get("objects", []), target)
        active_objects = _active_objects(s)
        sit = _find(active_objects, target)
        if item is None and sit is None:
            conflicts.append({"type": "unknown_object", "target": target, "op": op})
            continue

        if op == "remove":
            if item is not None:
                m["objects"].remove(item)
            if sit is not None:
                active_objects.remove(sit)
            applied.append(dict(edit))
            continue

        if op == "move":
            if item is None:
                conflicts.append({"type": "unmeasured_object", "target": target, "op": "move"})
                continue
            dx, dy = float(edit.get("dx") or 0.0), float(edit.get("dy") or 0.0)
            moved = {**item, "center": [item["center"][0] + dx, item["center"][1] + dy, item["center"][2]]}
            corners = _rect_of_measurement(moved)
            if not _inside_room(corners, bounds):
                conflicts.append({"type": "out_of_room", "target": target, "op": "move"})
                continue
            hit = None
            for other in m.get("objects", []):
                if object_id(other) == target:
                    continue
                depth = overlap_depth(corners, _rect_of_measurement(other))
                if depth > _EDGE_TOL:
                    hit = {"id": object_id(other), "depth": depth}
                    break
            if hit:
                conflicts.append({"type": "overlap", "target": target, "other": hit["id"],
                                  "overlap_m": round(hit["depth"], 3), "op": "move"})
                continue
            item["center"] = moved["center"]
            if sit is not None:
                sit["center"] = [moved["center"][0], moved["center"][1], sit["center"][2]]
            else:
                active_objects.append({
                    "instance_id": object_id(item),
                    "label": item.get("type") or item.get("label") or "object",
                    "center": list(moved["center"]),
                    "size": [float(item.get("length_m") or 0.0),
                             float(item.get("width_m") or 0.0),
                             float(item.get("height_m") or 0.0)],
                    "rotation_z_deg": float(item.get("rotation_z_deg") or 0.0),
                })
            applied.append(dict(edit))
            continue

        if op == "rotate":
            if item is None:
                conflicts.append({"type": "unmeasured_object", "target": target, "op": "rotate"})
                continue
            turns = int(edit.get("quarter_turns") or 1) % 4
            if turns == 0:
                continue
            new_rotation = (float(item.get("rotation_z_deg") or 0.0) + 90.0 * turns) % 360.0
            rotated = {**item, "rotation_z_deg": new_rotation}
            corners = _rect_of_measurement(rotated)
            if not _inside_room(corners, bounds):
                conflicts.append({"type": "out_of_room", "target": target, "op": "rotate"})
                continue
            hit = next((other for other in m.get("objects", [])
                        if object_id(other) != target
                        and overlap_depth(corners, _rect_of_measurement(other)) > _EDGE_TOL), None)
            if hit is not None:
                conflicts.append({"type": "overlap", "target": target, "other": object_id(hit),
                                  "op": "rotate"})
                continue
            item["rotation_z_deg"] = new_rotation
            mirrored = {"instance_id": target, "label": item.get("type") or item.get("label") or "object",
                        "center": list(item["center"]),
                        "size": [item["length_m"], item["width_m"], float(item.get("height_m") or 0.0)],
                        "rotation_z_deg": new_rotation}
            if sit is None:
                active_objects.append(mirrored)
            else:
                sit["center"] = mirrored["center"]
                if not sit.get("size"):
                    sit["size"] = mirrored["size"]
                sit["rotation_z_deg"] = (float(sit.get("rotation_z_deg") or 0.0) + 90.0 * turns) % 360.0
            applied.append(dict(edit))
            continue

        conflicts.append({"type": "unsupported_op", "target": target, "op": op})

    return {"measurements": m, "structure": s, "conflicts": conflicts,
            "applied_edits": applied, "assumed_ids": assumed_ids}
