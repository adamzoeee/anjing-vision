"""Assemble formal metrics and paths from existing structured artifacts."""
from __future__ import annotations

from pipeline.spatial_layout import (
    extract_activity_area_metric,
    extract_bedside_clearance_metric,
    extract_bed_surrounding_space_metric,
    extract_crowding_metric,
    extract_furniture_spacing_metric,
    extract_main_activity_area_safety_metric,
    extract_wall_clearance_metrics,
)
from pipeline.spatial_metrics import (
    build_metric_payload,
    extract_door_width_metric,
    extract_entrance_space_metric,
    extract_passage_width_metrics,
)
from pipeline.spatial_paths import extract_path_metrics, normalize_paths


def build_spatial_assessment_inputs(
    measurements: dict,
    passage: dict,
    foundation: dict,
) -> dict:
    """Build the complete second-stage assessment input without raw-data access."""
    paths = normalize_paths(passage, foundation)
    activity_area = extract_activity_area_metric(foundation)
    metrics = [
        *extract_passage_width_metrics(passage, foundation),
        extract_door_width_metric(measurements, passage),
        extract_entrance_space_metric(passage),
        *extract_path_metrics(paths),
        extract_furniture_spacing_metric(passage),
        *extract_wall_clearance_metrics(foundation),
        extract_bedside_clearance_metric(passage, foundation),
        activity_area,
        extract_crowding_metric(foundation),
        extract_bed_surrounding_space_metric(passage, foundation),
        extract_main_activity_area_safety_metric(activity_area, paths),
    ]
    payload = build_metric_payload(metrics)
    payload["paths"] = paths
    payload["scope"] = {
        "structured_inputs_only": True,
        "raw_media_accessed": False,
        "point_cloud_accessed": False,
        "risk_rules_applied": False,
    }
    return payload
