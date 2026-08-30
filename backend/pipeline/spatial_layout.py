"""Layout metrics derived exclusively from structured room/furniture records."""
from __future__ import annotations

import math

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
