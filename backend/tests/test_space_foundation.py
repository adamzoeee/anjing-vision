import copy
import json

from pipeline.space_foundation import (
    _geometric_passage_class,
    analyze_structure_passages,
    build_space_foundation_files,
)


def _inputs():
    structure = {
        "room": {
            "bounds_xy": {"min": [0.0, 0.0], "max": [4.0, 3.0]},
            "floor_polygon": [[0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0]],
        }
    }
    measurements = {
        "room": {
            "length_m": 4.0, "width_m": 3.0, "height_m": 2.6,
            "measurement_status": "verified", "confidence": "high",
        },
        "openings": [{
            "id": "door_01", "type": "door", "center": [2.0, 0.0, 1.05],
            "width_m": 0.9, "height_m": 2.1, "measurement_status": "verified",
            "confidence": "high", "rotation_z_deg": 0.0,
        }],
        "objects": [
            {
                "instance_id": "bed_001", "type": "bed", "center": [2.0, 2.4, 0.3],
                "length_m": 1.8, "width_m": 0.9, "height_m": 0.6,
                "rotation_z_deg": 0.0, "measurement_status": "verified", "confidence": "high",
            },
            {
                "instance_id": "desk_001", "type": "desk", "center": [0.65, 1.5, 0.4],
                "length_m": 1.0, "width_m": 0.55, "height_m": 0.8,
                "rotation_z_deg": 90.0, "measurement_status": "verified", "confidence": "medium",
            },
        ],
    }
    return measurements, structure


def test_structure_only_analysis_finds_door_to_bed_route():
    measurements, structure = _inputs()
    report = analyze_structure_passages(measurements, structure)
    route = report["primary_route"]
    assert report["analysis_basis"] == "existing_2d_structure_only"
    assert route["path_exists"] is True
    assert route["path_length_m"] > 1.0
    assert route["minimum_clear_width_m"] > 0.0
    assert isinstance(route["can_person_pass"], bool)
    assert route["requirement_status"] == "pending_rule_definition"
    assert report["risk_scoring_included"] is False
    assert report["walkable_regions"]["door_connected_area_m2"] > 0.0
    assert report["furniture_clearances"][0]["geometry_relation"] == "separated"
    assert report["analysis_profile"]["direct_pass_width_m"] == 0.45
    assert report["analysis_profile"]["sideways_pass_width_m"] == 0.30
    assert route["geometric_passage_class"] in {
        "normal_pass", "sideways_pass", "not_passable", "unknown",
    }


def test_geometric_passage_tiers_are_not_risk_scores():
    assert _geometric_passage_class(0.50, 0.45, 0.30) == "normal_pass"
    assert _geometric_passage_class(0.35, 0.45, 0.30) == "sideways_pass"
    assert _geometric_passage_class(0.20, 0.45, 0.30) == "not_passable"


def test_file_builder_does_not_modify_structure_or_measurements(tmp_path):
    measurements, structure = _inputs()
    measurement_path = tmp_path / "measurements.json"
    structure_path = tmp_path / "structure.json"
    measurement_path.write_text(json.dumps(measurements), encoding="utf-8")
    structure_path.write_text(json.dumps(structure), encoding="utf-8")
    before_measurements = copy.deepcopy(measurements)
    before_structure = copy.deepcopy(structure)

    outputs = build_space_foundation_files(measurement_path, structure_path, tmp_path / "outputs")

    assert json.loads(measurement_path.read_text(encoding="utf-8")) == before_measurements
    assert json.loads(structure_path.read_text(encoding="utf-8")) == before_structure
    assert outputs["passage_analysis"].is_file()
    assert outputs["spatial_foundation"].is_file()
    assert outputs["passage_figure"].is_file()
    foundation = json.loads(outputs["spatial_foundation"].read_text(encoding="utf-8"))
    assert foundation["room"]["area_m2"] == 12.0
    assert foundation["scope"]["risk_score_included"] is False
    assert foundation["provenance"]["inputs_modified"] is False
