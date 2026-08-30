"""Normalize precomputed structure-only paths for formal assessment."""
from __future__ import annotations

from typing import Any

from pipeline.spatial_metrics import confidence_value


PATH_STATUSES = frozenset({"complete", "blocked", "not_evaluable"})


def _normalized_path(route: dict, *, fallback_id: str) -> dict:
    path_exists = route.get("path_exists")
    if path_exists is True:
        status, reason = "complete", None
    elif path_exists is False:
        status = "blocked"
        reason = route.get("reason") or "no_geometric_path_in_current_structure"
    else:
        status = "not_evaluable"
        reason = route.get("reason") or "path_evidence_unavailable"
    width = route.get("minimum_clear_width_m")
    point = route.get("narrowest_point_xy")
    return {
        "path_id": route.get("id") or fallback_id,
        "start": route.get("from"),
        "target": route.get("to"),
        "status": status,
        "length_m": route.get("path_length_m") if status == "complete" else None,
        "continuous": True if status == "complete" else (False if status == "blocked" else None),
        "detour": route.get("detour"),
        "obstructed": route.get("path_blocked") if route.get("path_blocked") is not None else (
            False if status == "complete" else (True if status == "blocked" else None)
        ),
        "bottleneck": {
            "width_m": width,
            "position_xy": point,
        } if width is not None or point is not None else None,
        "confidence": confidence_value(route.get("confidence")),
        "reason": reason,
    }


def _activity_anchor(foundation: dict) -> dict | None:
    for item in foundation.get("furniture", []):
        if item.get("type") in {"activity_area", "activity_anchor"} and item.get("id"):
            return item
    return None


def normalize_paths(passage: dict, foundation: dict) -> list[dict[str, Any]]:
    """Return explicit path records without creating new geometric routes."""
    routes = list(foundation.get("passages") or [])
    primary = passage.get("primary_route") or {}
    if primary and not any(item.get("id") == primary.get("id") for item in routes):
        routes.insert(0, primary)
    paths = [
        _normalized_path(route, fallback_id=f"path_{index + 1:03d}")
        for index, route in enumerate(routes)
        if route
    ]

    activity = _activity_anchor(foundation)
    activity_route = next((item for item in paths if item["path_id"] == "entrance_to_activity"), None)
    if activity_route is None:
        reason = "activity_route_unavailable" if activity else "explicit_activity_anchor_missing"
        paths.append({
            "path_id": "entrance_to_activity",
            "start": primary.get("from"),
            "target": activity.get("id") if activity else None,
            "status": "not_evaluable",
            "length_m": None,
            "continuous": None,
            "detour": None,
            "obstructed": None,
            "bottleneck": None,
            "confidence": None,
            "reason": reason,
        })
    return paths
