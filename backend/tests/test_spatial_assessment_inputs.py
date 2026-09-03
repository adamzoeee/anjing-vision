import hashlib
import json

import pytest

from pipeline.spatial_assessment_inputs import (
    build_spatial_assessment_input_file,
    build_spatial_assessment_inputs,
    build_formal_assessment_files,
)


def _structured_inputs():
    route = {
        "id": "door_to_bed", "from": "door_01", "to": "bed_001",
        "path_exists": True, "path_blocked": False, "path_length_m": 1.5,
        "minimum_clear_width_m": 0.72, "narrowest_point_xy": [2, 1],
    }
    measurements = {"openings": [{
        "id": "door_01", "type": "door", "width_m": 0.86,
        "measurement_status": "verified", "confidence": "high", "center": [2, 0, 1],
    }]}
    passage = {
        "status": "ok", "primary_route": route,
        "walkable_regions": {"door_connected_area_m2": 4.0},
        "furniture_clearances": [{
            "between": ["bed_001", "desk_001"], "clearance_m": 0.45,
        }],
    }
    foundation = {
        "room": {
            "area_m2": 12.0,
            "floor_polygon": [[0, 0], [4, 0], [4, 3], [0, 3]],
        },
        "passages": [route],
        "furniture": [
            {
                "id": "bed_001", "type": "bed", "position_xyz": [2, 2, 0.3],
                "length_m": 2, "width_m": 1, "confidence": "high",
            },
            {
                "id": "desk_001", "type": "desk", "position_xyz": [0.7, 1.5, 0.4],
                "length_m": 1, "width_m": 0.5,
            },
        ],
    }
    return measurements, passage, foundation


def test_assessment_input_contains_all_formal_metrics_and_paths():
    payload = build_spatial_assessment_inputs(*_structured_inputs())
    assert len(payload["metrics"]) == 15
    assert len({item["metric_code"] for item in payload["metrics"]}) == 15
    assert payload["scope"] == {
        "structured_inputs_only": True,
        "raw_media_accessed": False,
        "point_cloud_accessed": False,
        "risk_rules_applied": False,
    }
    activity_path = next(item for item in payload["paths"] if item["path_id"] == "entrance_to_activity")
    assert activity_path["status"] == "not_evaluable"


def test_assessment_input_keeps_missing_activity_evidence_in_coverage():
    payload = build_spatial_assessment_inputs(*_structured_inputs())
    by_code = {item["metric_code"]: item for item in payload["metrics"]}
    assert by_code["activity_area"]["reason"] == "explicit_activity_anchor_missing"
    assert by_code["main_activity_area_safety"]["status"] == "not_evaluable"
    assert payload["coverage"]["not_evaluable_count"] >= 2


def test_file_builder_reads_only_json_and_preserves_input_hashes(tmp_path):
    inputs = _structured_inputs()
    paths = []
    for name, value in zip(("measurements", "passage_analysis", "spatial_foundation"), inputs):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(path)
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }
    output = tmp_path / "spatial_metrics.json"
    payload = build_spatial_assessment_input_file(*paths, output)
    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }
    assert before == after
    assert output.is_file()
    assert payload["provenance"]["inputs_modified"] is False
    assert {item["artifact"] for item in payload["provenance"]["inputs"]} == set(before)


def test_file_builder_rejects_non_json_inputs(tmp_path):
    ply = tmp_path / "scene.ply"
    ply.write_text("ply")
    with pytest.raises(ValueError, match="must be JSON"):
        build_spatial_assessment_input_file(ply, ply, ply, tmp_path / "output.json")


def test_formal_assessment_file_chain_produces_single_backend_payload(tmp_path):
    measurements, _, _ = _structured_inputs()
    structure = {
        "room": {
            "bounds_xy": {"min": [0, 0], "max": [4, 3]},
            "floor_polygon": [[0, 0], [4, 0], [4, 3], [0, 3]],
        },
        "semantic_instances": [],
    }
    measurements_path = tmp_path / "measurements.json"
    structure_path = tmp_path / "structure.json"
    measurements_path.write_text(json.dumps(measurements), encoding="utf-8")
    structure_path.write_text(json.dumps(structure), encoding="utf-8")
    outputs = build_formal_assessment_files(
        measurements_path, structure_path, tmp_path / "postprocess",
    )
    assert outputs["spatial_metrics"].is_file()
    assert outputs["risk_assessment"].is_file()
    assessment = json.loads(outputs["risk_assessment"].read_text(encoding="utf-8"))
    assert assessment["official"] is True
    assert len(assessment["metrics"]) == 15
    assert len(assessment["risks"]) == 15
