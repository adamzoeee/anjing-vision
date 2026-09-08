"""Layout metrics derived exclusively from structured room/furniture records."""
from __future__ import annotations

import math

import numpy as np

from pipeline.spatial_metrics import build_metric, confidence_value, unavailable_metric


def extract_furniture_spacing_metric(passage: dict) -> dict:
    """Return the minimum reported clearance between distinct furniture instances."""
    source = {"artifact": "passage_analysis.json", "field": "furniture_clearances[*]"}
    candidates = [
        item for item in passage.get("furniture_clearances", [])
        if item.get("clearance_m") is not None and len(item.get("between") or []) == 2
    ]
    if not candidates:
        return unavailable_metric("furniture_spacing", "furniture_clearance_unavailable", source=source)
    narrowest = min(candidates, key=lambda item: float(item["clearance_m"]))
    return build_metric(
        "furniture_spacing",
        value=round(float(narrowest["clearance_m"]), 3),
        status="derived",
        confidence=confidence_value(narrowest.get("confidence")),
        position={
            "object_ids": list(narrowest["between"]),
            "labels": list(narrowest.get("between_labels") or []),
        },
        source=source,
    )


def _footprint_corners(item: dict) -> list[tuple[float, float]]:
    center = item.get("position_xyz") or item.get("center")
    length, width = item.get("length_m"), item.get("width_m")
    if not center or length is None or width is None:
        return []
    yaw = math.radians(float(item.get("rotation_z_deg") or 0.0))
    cos_yaw, sin_yaw = math.cos(yaw), math.sin(yaw)
    corners = []
    for dx, dy in ((-length / 2, -width / 2), (length / 2, -width / 2),
                   (length / 2, width / 2), (-length / 2, width / 2)):
        corners.append((
            float(center[0]) + dx * cos_yaw - dy * sin_yaw,
            float(center[1]) + dx * sin_yaw + dy * cos_yaw,
        ))
    return corners


def _point_segment_distance(point, start, end) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(px - sx, py - sy)
    ratio = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / length_sq))
    return math.hypot(px - (sx + ratio * dx), py - (sy + ratio * dy))


def _wall_distance(item: dict, floor_polygon: list) -> float | None:
    corners = _footprint_corners(item)
    floor = [(float(point[0]), float(point[1])) for point in floor_polygon if len(point) >= 2]
    if not corners or len(floor) < 3:
        return None
    return min(
        _point_segment_distance(corner, floor[index], floor[(index + 1) % len(floor)])
        for corner in corners
        for index in range(len(floor))
    )


def extract_wall_clearance_metrics(foundation: dict) -> list[dict]:
    """Derive furniture-to-wall and bed-to-wall distances from accepted 2D boxes."""
    source = {"artifact": "spatial_foundation.json", "field": "room.floor_polygon+furniture"}
    floor = (foundation.get("room") or {}).get("floor_polygon") or []
    furniture = foundation.get("furniture") or []
    distances = [
        (item, distance) for item in furniture
        if (distance := _wall_distance(item, floor)) is not None
    ]
    if distances:
        item, distance = min(distances, key=lambda pair: pair[1])
        wall_metric = build_metric(
            "wall_furniture_clearance", value=round(distance, 3), status="derived",
            confidence=confidence_value(item.get("confidence")),
            position={"object_id": item.get("id")}, source=source,
        )
    else:
        wall_metric = unavailable_metric(
            "wall_furniture_clearance", "room_or_furniture_geometry_unavailable", source=source,
        )
    bed_distances = [(item, distance) for item, distance in distances if item.get("type") == "bed"]
    if bed_distances:
        item, distance = min(bed_distances, key=lambda pair: pair[1])
        bed_metric = build_metric(
            "bed_wall_distance", value=round(distance, 3), status="derived",
            confidence=confidence_value(item.get("confidence")),
            position={"object_id": item.get("id")}, source=source,
        )
    else:
        bed_metric = unavailable_metric("bed_wall_distance", "verified_bed_geometry_unavailable", source=source)
    return [wall_metric, bed_metric]


def extract_bedside_clearance_metric(passage: dict, foundation: dict) -> dict:
    """Return the minimum structured clearance from a verified bed to furniture."""
    source = {"artifact": "passage_analysis.json", "field": "furniture_clearances[*]"}
    bed_ids = {
        item.get("id") for item in foundation.get("furniture", [])
        if item.get("type") == "bed" and item.get("id")
    }
    if not bed_ids:
        return unavailable_metric("bedside_clearance", "verified_bed_geometry_unavailable", source=source)
    candidates = []
    for item in passage.get("furniture_clearances", []):
        pair = list(item.get("between") or [])
        if len(pair) == 2 and bed_ids.intersection(pair) and item.get("clearance_m") is not None:
            candidates.append(item)
    if not candidates:
        return unavailable_metric("bedside_clearance", "bedside_clearance_unavailable", source=source)
    nearest = min(candidates, key=lambda item: float(item["clearance_m"]))
    return build_metric(
        "bedside_clearance", value=round(float(nearest["clearance_m"]), 3), status="derived",
        confidence=confidence_value(nearest.get("confidence")),
        position={"object_ids": list(nearest["between"])}, source=source,
    )


def extract_activity_area_metric(passage: dict) -> dict:
    """Use the continuous free-floor component reachable from the entrance."""
    source = {"artifact": "passage_analysis.json", "field": "walkable_regions.door_connected_area_m2"}
    route = passage.get("primary_route") or {}
    walkable = passage.get("walkable_regions") or {}
    area = walkable.get("door_connected_free_area_m2")
    if area is None:
        area = walkable.get("door_connected_area_m2")
    if area is None or not route.get("from"):
        return unavailable_metric("activity_area", "reachable_free_area_unavailable", source=source)
    return build_metric(
        "activity_area", value=round(float(area), 3), status="derived",
        confidence=confidence_value(walkable.get("confidence")),
        position={"object_id": route.get("from"), "region": "entrance_connected_free_floor"},
        source=source,
    )


def _furniture_union_area(furniture: list[dict], room_area: float) -> float:
    """Rasterize rotated footprints at 1cm and count the union once."""
    valid = [item for item in furniture if item.get("length_m") is not None and item.get("width_m") is not None]
    if not valid:
        return 0.0
    positioned = [item for item in valid if item.get("position_xyz") or item.get("center")]
    if len(positioned) != len(valid):
        return sum(float(item["length_m"]) * float(item["width_m"]) for item in valid)
    corners = [point for item in positioned for point in _footprint_corners(item)]
    lo = np.min(np.asarray(corners), axis=0)
    hi = np.max(np.asarray(corners), axis=0)
    cell = max(0.01, math.sqrt(max(room_area, 1e-6) / 1_000_000))
    xs = np.arange(lo[0] + cell / 2, hi[0], cell)
    ys = np.arange(lo[1] + cell / 2, hi[1], cell)
    xx, yy = np.meshgrid(xs, ys)
    occupied = np.zeros(xx.shape, dtype=bool)
    for item in positioned:
        center = item.get("position_xyz") or item.get("center")
        dx, dy = xx - float(center[0]), yy - float(center[1])
        yaw = math.radians(float(item.get("rotation_z_deg") or 0.0))
        local_x = dx * math.cos(yaw) + dy * math.sin(yaw)
        local_y = -dx * math.sin(yaw) + dy * math.cos(yaw)
        occupied |= ((np.abs(local_x) <= float(item["length_m"]) / 2)
                     & (np.abs(local_y) <= float(item["width_m"]) / 2))
    return float(occupied.sum()) * cell * cell


def extract_crowding_metric(foundation: dict) -> dict:
    """Compute accepted furniture footprint area divided by structured room area."""
    source = {"artifact": "spatial_foundation.json", "field": "room.area_m2+furniture[*]"}
    room_area = (foundation.get("room") or {}).get("area_m2")
    if room_area is None or float(room_area) <= 0:
        return unavailable_metric("crowding", "verified_room_area_unavailable", source=source)
    furniture = foundation.get("furniture", [])
    if not any(item.get("length_m") is not None and item.get("width_m") is not None for item in furniture):
        return unavailable_metric("crowding", "verified_furniture_footprints_unavailable", source=source)
    furniture_area = _furniture_union_area(furniture, float(room_area))
    ratio = furniture_area / float(room_area)
    if ratio > 1.0:
        return unavailable_metric("crowding", "furniture_footprint_area_exceeds_room_area", source=source)
    return build_metric(
        "crowding", value=round(ratio, 4), status="derived", confidence=None,
        position={"room": True, "furniture_area_m2": round(furniture_area, 3),
                  "room_area_m2": round(float(room_area), 3)}, source=source,
    )


def extract_bed_surrounding_space_metric(passage: dict, foundation: dict) -> dict:
    """Return the most constrained observed clearance around a verified bed."""
    source = {
        "artifacts": ["passage_analysis.json", "spatial_foundation.json"],
        "fields": ["furniture_clearances[*]", "room.floor_polygon+furniture"],
    }
    floor = (foundation.get("room") or {}).get("floor_polygon") or []
    beds = [item for item in foundation.get("furniture", []) if item.get("type") == "bed"]
    if not beds:
        return unavailable_metric(
            "bed_surrounding_space", "verified_bed_geometry_unavailable", source=source,
        )
    candidates: list[tuple[float, dict]] = []
    for bed in beds:
        wall_distance = _wall_distance(bed, floor)
        if wall_distance is not None:
            candidates.append((wall_distance, {"object_id": bed.get("id"), "boundary": "wall"}))
        for relation in passage.get("furniture_clearances", []):
            pair = list(relation.get("between") or [])
            if bed.get("id") in pair and relation.get("clearance_m") is not None:
                candidates.append((
                    float(relation["clearance_m"]),
                    {"object_ids": pair, "boundary": "furniture"},
                ))
    if not candidates:
        return unavailable_metric(
            "bed_surrounding_space", "bed_surrounding_clearance_unavailable", source=source,
        )
    distance, position = min(candidates, key=lambda pair: pair[0])
    bed_confidence = min(
        (value for value in (confidence_value(item.get("confidence")) for item in beds) if value is not None),
        default=None,
    )
    return build_metric(
        "bed_surrounding_space", value=round(distance, 3), status="derived",
        confidence=bed_confidence, position=position, source=source,
    )


def extract_main_activity_area_safety_metric(activity_metric: dict, paths: list[dict]) -> dict:
    """Combine reachable free area with the entrance-to-bed route state."""
    source = {
        "artifacts": ["spatial_metrics", "normalized_paths"],
        "fields": ["activity_area", "entrance_to_activity"],
    }
    if activity_metric.get("status") == "not_evaluable":
        return unavailable_metric(
            "main_activity_area_safety",
            activity_metric.get("reason") or "activity_area_unavailable",
            source=source,
        )
    path = next((item for item in paths if item.get("path_id") == "entrance_to_activity"
                 and item.get("status") != "not_evaluable"), None)
    if path is None:
        path = next((item for item in paths if item.get("path_id") != "entrance_to_activity"), None)
    if path is None or path.get("status") == "not_evaluable":
        return unavailable_metric(
            "main_activity_area_safety",
            (path or {}).get("reason") or "activity_route_unavailable",
            source=source,
        )
    bottleneck = (path.get("bottleneck") or {}).get("width_m")
    safe_evidence = (
        bool(path.get("continuous"))
        and not bool(path.get("obstructed"))
        and (bottleneck is None or float(bottleneck) >= 0.30)
    )
    confidence_candidates = [
        value for value in (activity_metric.get("confidence"), path.get("confidence"))
        if value is not None
    ]
    return build_metric(
        "main_activity_area_safety", value=safe_evidence, status="derived",
        confidence=min(confidence_candidates) if confidence_candidates else None,
        position={
            "object_id": activity_metric.get("position", {}).get("object_id"),
            "path_id": path.get("path_id"),
        },
        source=source,
    )
