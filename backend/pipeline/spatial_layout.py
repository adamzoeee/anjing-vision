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


def extract_activity_area_metric(foundation: dict) -> dict:
    """Measure only an explicitly labelled activity anchor, never room centre."""
    source = {"artifact": "spatial_foundation.json", "field": "furniture[*]"}
    anchor = next((
        item for item in foundation.get("furniture", [])
        if item.get("type") in {"activity_area", "activity_anchor"} and item.get("id")
    ), None)
    if anchor is None:
        return unavailable_metric("activity_area", "explicit_activity_anchor_missing", source=source)
    area = anchor.get("area_m2")
    if area is None and anchor.get("length_m") is not None and anchor.get("width_m") is not None:
        area = float(anchor["length_m"]) * float(anchor["width_m"])
    if area is None or float(area) <= 0:
        return unavailable_metric("activity_area", "activity_anchor_geometry_unavailable", source=source)
    return build_metric(
        "activity_area", value=round(float(area), 3), status="derived",
        confidence=confidence_value(anchor.get("confidence")),
        position={"object_id": anchor.get("id"), "center_xyz": anchor.get("position_xyz")},
        source=source,
    )


def extract_crowding_metric(foundation: dict) -> dict:
    """Compute accepted furniture footprint area divided by structured room area."""
    source = {"artifact": "spatial_foundation.json", "field": "room.area_m2+furniture[*]"}
    room_area = (foundation.get("room") or {}).get("area_m2")
    if room_area is None or float(room_area) <= 0:
        return unavailable_metric("crowding", "verified_room_area_unavailable", source=source)
    footprints = [
        float(item["length_m"]) * float(item["width_m"])
        for item in foundation.get("furniture", [])
        if item.get("length_m") is not None and item.get("width_m") is not None
    ]
    if not footprints:
        return unavailable_metric("crowding", "verified_furniture_footprints_unavailable", source=source)
    ratio = sum(footprints) / float(room_area)
    if ratio > 1.0:
        return unavailable_metric("crowding", "furniture_footprint_area_exceeds_room_area", source=source)
    return build_metric(
        "crowding", value=round(ratio, 4), status="derived", confidence=None,
        position={"room": True}, source=source,
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
    """Summarize explicit activity-area reachability without applying risk thresholds."""
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
    path = next((item for item in paths if item.get("path_id") == "entrance_to_activity"), None)
    if path is None or path.get("status") == "not_evaluable":
        return unavailable_metric(
            "main_activity_area_safety",
            (path or {}).get("reason") or "activity_route_unavailable",
            source=source,
        )
    safe_evidence = bool(path.get("continuous")) and not bool(path.get("obstructed"))
    confidence_candidates = [
        value for value in (activity_metric.get("confidence"), path.get("confidence"))
        if value is not None
    ]
    return build_metric(
        "main_activity_area_safety", value=safe_evidence, status="derived",
        confidence=min(confidence_candidates) if confidence_candidates else None,
        position={
            "object_id": activity_metric.get("position", {}).get("object_id"),
            "path_id": "entrance_to_activity",
        },
        source=source,
    )
