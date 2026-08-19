import json

import pytest

from pipeline.measurement_builder import build_measurements, build_risk_inputs
from pipeline.passage_builder import build_passage_metrics
from pipeline.rules import compute_score, evaluate_risks


def _structure(*, ready=True, geometry_status="verified", instance_status="stable"):
    instance = {
        "instance_id": "bed_001", "normalized_label": "bed", "semantic_label": "床",
        "support_views": 4, "status": instance_status, "instance_confidence": 0.9,
        "geometry_status": geometry_status, "geometry_confidence": 0.8,
        "measurement_ready": ready,
        "bbox": {"center": [0, 0, 0.5], "size": [2, 1, 1]},
        "center": [0, 0, 0.5], "size": [2, 1, 1], "rotation_z_deg": 0,
        "dimensions": {"length": 2, "width": 1, "height": 1},
    }
    return {
        "room": {"bounds_xy": {"min": [0, 0], "max": [4, 3]}, "height_m": 2.5},
        "doors": [{
            "center": [0, 1, 1], "size": [1, 0.1, 2],
            "geometry_status": "verified", "geometry_confidence": 0.9,
        }],
        "objects": [{"instance_id": "legacy_bed", "label": "bed", "center": [1, 1, 0.5], "size": [2, 1, 1]}],
        "semantic_instances": [instance],
    }


def _references():
    return [
        {"object_type": "bed", "dimension": "length", "meters": 2.0},
        {"object_type": "door", "dimension": "height", "meters": 2.0},
    ]


def test_verified_chain_and_metric_scale_produces_formal_measurement():
    result = build_measurements(_structure(), _references())
    item = result["objects"][0]
    assert result["metric_scale_available"] is True
    assert item["measurement_status"] == "verified"
    assert (item["length_m"], item["width_m"], item["height_m"]) == (2.0, 1.0, 1.0)
    assert result["measurement_coverage"]["verified_count"] == 1


def test_bbox_without_measurement_ready_is_not_formal_measurement():
    result = build_measurements(_structure(ready=False), _references())
    item = result["objects"][0]
    assert item["measurement_status"] == "unavailable"
    assert item["measurement_reason"] == "incomplete_instance_geometry"
    assert item["length_m"] is None
    assert result["diagnostics"]["objects"][0]["observed_geometry_scene_units"]["bbox"] is not None


def test_low_confidence_geometry_is_unavailable():
    item = build_measurements(
        _structure(ready=False, geometry_status="low_confidence"), _references(),
    )["objects"][0]
    assert item["measurement_status"] == "unavailable"
    assert item["measurement_reason"] == "geometry_not_verified"


def test_forced_legacy_mode_uses_single_accepted_reference_and_tentative_geometry():
    structure = _structure(ready=False, geometry_status="low_confidence")
    result = build_measurements(
        structure,
        _references()[:1],
        force_legacy_measurements=True,
        geometry_diagnostics={"instances": [{
            "instance_id": "bed_001", "length": 1.8, "width": 1.3, "height": 0.4,
            "rotation": 12.0,
            "xy_rectangle": {"center_xy": [1.0, 1.0]},
            "z_range": {"z_bottom": 0.0, "z_top": 0.4},
            "reason": "geometry_confidence_too_low",
        }]},
    )
    item = next(item for item in result["objects"] if item["instance_id"] == "bed_001")
    assert result["metric_scale_available"] is True
    assert result["scale"]["forced_estimate"] is True
    assert item["measurement_status"] == "verified"
    assert item["forced_estimate"] is True
    assert item["risk_eligibility"] == "not_evaluable"
    assert item["length_m"] == pytest.approx(1.8)


def test_forced_legacy_measurement_never_enters_risk_score():
    result = build_measurements(
        _structure(ready=False, geometry_status="low_confidence"),
        _references()[:1],
        force_legacy_measurements=True,
        geometry_diagnostics={"instances": [{
            "instance_id": "bed_001", "length": 1.8, "width": 1.3, "height": 0.4,
        }]},
    )
    forced = next(item for item in result["objects"] if item.get("forced_estimate"))
    assert forced["risk_eligibility"] == "not_evaluable"
    assert build_risk_inputs(result)["passage_width_m"] is None


def test_scale_failure_continues_without_fake_metric_values():
    result = build_measurements(_structure(), _references()[:1])
    assert result["scale"]["status"] == "failed"
    assert result["metric_scale_available"] is False
    assert result["coordinate_unit"] == "scene_units"
    assert result["room"]["length_m"] is None
    assert result["openings"][0]["width_m"] is None
    assert result["objects"][0]["length_m"] is None


def test_unavailable_measurement_does_not_enter_risk_score():
    result = build_measurements(_structure(ready=False), _references()[:1])
    score, detail = compute_score(build_risk_inputs(result), include_not_evaluable=True)
    assert score is None
    assert all(item["assessment_status"] == "not_evaluable" for item in detail["risks"])


def test_not_evaluable_risk_is_not_safe():
    risks = evaluate_risks(
        {"door_width_m": None, "risk_eligibility": {
            "door_width": {"status": "not_evaluable", "reason": "scale_unavailable"},
        }},
        include_not_evaluable=True,
    )
    door = next(item for item in risks if item["code"] == "door_width")
    assert door["assessment_status"] == "not_evaluable"
    assert door["level"] == "unknown"
    assert door["reason"] == "scale_unavailable"


def test_programming_error_is_not_swallowed(tmp_path):
    with pytest.raises((TypeError, ValueError)):
        build_measurements(_structure(), [
            {"object_type": "bed", "dimension": "length", "meters": "not-a-number"},
            {"object_type": "door", "dimension": "height", "meters": 2.0},
        ])

    measurements = build_measurements(_structure(), _references())
    measurements["scale"]["scale_factor"] = 0
    structure_path = tmp_path / "structure.json"
    measurements_path = tmp_path / "measurements.json"
    structure_path.write_text(json.dumps(_structure()), encoding="utf-8")
    measurements_path.write_text(json.dumps(measurements), encoding="utf-8")
    with pytest.raises(ValueError, match="scale_factor"):
        build_passage_metrics(tmp_path / "missing.ply", structure_path, measurements_path)
