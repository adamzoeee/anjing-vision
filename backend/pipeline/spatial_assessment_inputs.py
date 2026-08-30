"""Assemble formal metrics and paths from existing structured artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_spatial_assessment_input_file(
    measurements_json: Path,
    passage_json: Path,
    foundation_json: Path,
    output_json: Path,
) -> dict:
    """Read only approved JSON artifacts and persist the formal metric payload."""
    paths = [Path(measurements_json), Path(passage_json), Path(foundation_json)]
    if any(path.suffix.lower() != ".json" for path in paths):
        raise ValueError("formal assessment inputs must be JSON artifacts")
    measurements, passage, foundation = [
        json.loads(path.read_text(encoding="utf-8")) for path in paths
    ]
    payload = build_spatial_assessment_inputs(measurements, passage, foundation)
    payload["provenance"] = {
        "inputs": [
            {"artifact": path.name, "sha256": _sha256(path)} for path in paths
        ],
        "inputs_modified": False,
    }
    output_json = Path(output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def build_formal_assessment_files(
    measurements_json: Path,
    structure_json: Path,
    output_dir: Path,
) -> dict:
    """Create foundation, metrics, and risk JSON files from approved structure data."""
    from pipeline.risk_assessment import build_risk_assessment_file
    from pipeline.space_foundation import build_space_foundation_files

    output_dir = Path(output_dir)
    foundation_outputs = build_space_foundation_files(
        Path(measurements_json), Path(structure_json), output_dir,
    )
    metric_path = output_dir / "spatial_metrics.json"
    metric_payload = build_spatial_assessment_input_file(
        Path(measurements_json),
        foundation_outputs["passage_analysis"],
        foundation_outputs["spatial_foundation"],
        metric_path,
    )
    risk_path = output_dir / "risk_assessment.json"
    risk_payload = build_risk_assessment_file(metric_path, risk_path)
    return {
        **foundation_outputs,
        "spatial_metrics": metric_path,
        "risk_assessment": risk_path,
        "metric_payload": metric_payload,
        "risk_payload": risk_payload,
    }
