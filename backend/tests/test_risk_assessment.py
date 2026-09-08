import hashlib
import json

import pytest

from pipeline.risk_assessment import (
    RiskResult,
    build_risk_assessment,
    build_risk_assessment_file,
    collect_specific_advice,
    evaluate_formal_metrics,
    rank_top_risks,
    risk_result,
    score_formal_risks,
    summarize_assessment_confidence,
)
from pipeline.spatial_metrics import METRIC_DEFINITION_BY_CODE, build_metric, unavailable_metric


def _values():
    return {
        "risk_code": "narrow_main_passage",
        "risk_type": "mobility",
        "risk_name": "主要通道过窄",
        "metric_code": "main_passage_width",
        "measured_value": 0.72,
        "unit": "m",
        "threshold": {"medium": 0.9, "high": 0.8, "direction": "lower_is_worse"},
        "position": {"path_id": "door_to_bed", "point_xy": [1.2, 0.8]},
        "risk_level": "high",
        "confidence": 0.8,
        "reason": "measured_below_high_risk_threshold",
        "advice": "移开通道两侧家具，优先把最窄处净宽提升到0.90米以上。",
        "assessment_status": "evaluated",
        "related_object_ids": [],
        "related_path_id": "door_to_bed",
    }


def test_risk_result_serializes_every_required_field():
    result = RiskResult(**_values()).to_dict()
    assert set(result) == {
        "risk_code", "risk_type", "risk_name", "metric_code", "measured_value",
        "unit", "threshold", "position", "risk_level", "confidence", "reason",
        "advice", "assessment_status", "related_object_ids", "related_path_id",
    }
    assert result["risk_level"] == "high"


def test_not_evaluable_risk_has_no_level_and_requires_reason():
    values = _values() | {
        "measured_value": None, "threshold": None, "risk_level": None,
        "reason": "metric_not_evaluable", "advice": None,
        "assessment_status": "not_evaluable",
    }
    assert risk_result(**values)["assessment_status"] == "not_evaluable"
    with pytest.raises(ValueError, match="require a reason"):
        RiskResult(**(values | {"reason": None}))
    with pytest.raises(ValueError, match="must not have a risk level"):
        RiskResult(**(values | {"risk_level": "low"}))


def test_risk_result_rejects_legacy_color_levels():
    with pytest.raises(ValueError, match="unsupported risk level"):
        RiskResult(**(_values() | {"risk_level": "red"}))


def _payload(overrides=None):
    overrides = overrides or {}
    metrics = []
    safe_values = {
        "main_passage_width": 1.0, "minimum_passage_width": 1.0, "door_width": 1.0,
        "entrance_space": 2.0, "path_length": 2.0, "path_continuity": True,
        "path_obstruction": False, "furniture_spacing": 0.8,
        "wall_furniture_clearance": 0.2, "bed_wall_distance": 0.2,
        "bedside_clearance": 0.8, "activity_area": 4.0, "crowding": 0.3,
        "bed_surrounding_space": 0.8, "main_activity_area_safety": True,
    }
    safe_values.update(overrides)
    for code in METRIC_DEFINITION_BY_CODE:
        value = safe_values[code]
        metrics.append(build_metric(code, value=value, status="derived", source="fixture"))
    return {"metrics": metrics}


def test_formal_evaluator_uses_highest_triggered_severity():
    risks = evaluate_formal_metrics(_payload({"door_width": 0.75}))
    door = next(item for item in risks if item["metric_code"] == "door_width")
    assert door["risk_level"] == "high"
    assert door["risk_code"] == "door_width_high"
    assert door["advice"]


def test_formal_evaluator_uses_low_medium_high_only():
    risks = evaluate_formal_metrics(_payload({
        "door_width": 0.85, "main_passage_width": 1.2,
    }))
    assert {item["risk_level"] for item in risks} <= {"low", "medium", "high"}
    assert next(item for item in risks if item["metric_code"] == "door_width")["risk_level"] == "medium"
    assert next(item for item in risks if item["metric_code"] == "main_passage_width")["risk_level"] == "low"


def test_formal_evaluator_never_turns_unknown_into_low_risk():
    payload = _payload()
    payload["metrics"] = [
        unavailable_metric(
            item["metric_code"], "fixture_missing", source="fixture",
        ) if item["metric_code"] == "activity_area" else item
        for item in payload["metrics"]
    ]
    risk = next(
        item for item in evaluate_formal_metrics(payload)
        if item["metric_code"] == "activity_area"
    )
    assert risk["assessment_status"] == "not_evaluable"
    assert risk["risk_level"] is None
    assert risk["reason"] == "fixture_missing"


def test_rule_boundaries_are_not_triggered_by_equality_for_directional_thresholds():
    risks = evaluate_formal_metrics(_payload({"door_width": 0.8, "crowding": 0.6}))
    assert next(item for item in risks if item["metric_code"] == "door_width")["risk_level"] == "medium"
    assert next(item for item in risks if item["metric_code"] == "crowding")["risk_level"] == "medium"


def test_official_score_is_100_when_every_metric_is_low_risk():
    result = score_formal_risks(evaluate_formal_metrics(_payload()))
    assert result["status"] == "evaluated"
    assert result["score"] == 100.0
    assert result["weights"] == {"mobility": 0.4, "layout": 0.3, "usage_safety": 0.3}


def test_official_score_applies_category_weights():
    risks = evaluate_formal_metrics(_payload({
        "main_passage_width": 0.7,
        "furniture_spacing": 0.2,
        "bed_surrounding_space": 0.3,
    }))
    result = score_formal_risks(risks)
    expected = round(sum(
        result["category_scores"][category]["score"] * weight
        for category, weight in result["weights"].items()
    ), 1)
    assert result["score"] == expected
    assert result["score"] < 100


def test_missing_core_metric_makes_overall_score_null():
    payload = _payload()
    payload["metrics"] = [
        unavailable_metric(item["metric_code"], "missing", source="fixture")
        if item["metric_code"] == "door_width" else item
        for item in payload["metrics"]
    ]
    result = score_formal_risks(evaluate_formal_metrics(payload))
    assert result["status"] == "insufficient_data"
    assert result["score"] is None
    assert result["missing_core_metrics"] == ["door_width"]


def test_noncore_unknown_is_excluded_without_becoming_safe():
    payload = _payload()
    payload["metrics"] = [
        unavailable_metric(item["metric_code"], "missing", source="fixture")
        if item["metric_code"] == "activity_area" else item
        for item in payload["metrics"]
    ]
    result = score_formal_risks(evaluate_formal_metrics(payload))
    assert result["status"] == "evaluated"
    assert result["category_scores"]["layout"]["not_evaluable_count"] == 1


def test_confidence_summary_separates_coverage_from_evidence_confidence():
    payload = _payload()
    payload["metrics"] = [
        (item | {"confidence": 0.8}) if item["metric_code"] != "activity_area"
        else unavailable_metric("activity_area", "missing", source="fixture")
        for item in payload["metrics"]
    ]
    risks = evaluate_formal_metrics(payload)
    summary = summarize_assessment_confidence(payload, risks)
    assert summary["assessment_coverage"]["evaluated_count"] == 14
    assert summary["assessment_coverage"]["not_evaluable_count"] == 1
    assert summary["evidence_confidence"] == 0.8
    assert summary["coverage_adjusted_confidence"] < 0.8


def test_missing_numeric_confidence_remains_null():
    payload = _payload()
    risks = evaluate_formal_metrics(payload)
    summary = summarize_assessment_confidence(payload, risks)
    assert summary["assessment_coverage"]["percent"] == 100.0
    assert summary["evidence_confidence"] is None
    assert summary["reason"] == "numeric_confidence_unavailable"


def test_top_risks_rank_high_before_medium_and_exclude_low_unknown():
    payload = _payload({
        "door_width": 0.75,
        "main_passage_width": 0.85,
        "furniture_spacing": 0.2,
    })
    payload["metrics"] = [
        unavailable_metric(item["metric_code"], "missing", source="fixture")
        if item["metric_code"] == "activity_area" else item
        for item in payload["metrics"]
    ]
    top = rank_top_risks(evaluate_formal_metrics(payload), limit=3)
    assert len(top) == 3
    assert [item["risk_level"] for item in top] == ["high", "high", "medium"]
    assert all(item["assessment_status"] == "evaluated" for item in top)


def test_actionable_risks_have_specific_deduplicated_advice():
    risks = evaluate_formal_metrics(_payload({
        "door_width": 0.75,
        "main_passage_width": 0.85,
    }))
    advice = collect_specific_advice(risks)
    assert len(advice) == 2
    assert len(set(advice)) == len(advice)
    assert any("门" in item for item in advice)
    assert any("通道" in item for item in advice)


def test_top_risk_limit_is_validated():
    with pytest.raises(ValueError, match="must not be negative"):
        rank_top_risks([], limit=-1)


def test_unified_assessment_contains_required_backend_sections():
    assessment = build_risk_assessment(_payload({
        "door_width": 0.75, "main_passage_width": 0.85,
    }))
    assert set(assessment) == {
        "schema_version", "official", "overall", "category_scores", "weights",
        "key_metrics", "metrics", "paths", "risks", "top_risks", "not_evaluable",
        "advice", "confidence", "provenance", "scope",
    }
    assert assessment["official"] is True
    assert assessment["overall"]["score"] is not None
    assert assessment["weights"] == {"mobility": 0.4, "layout": 0.3, "usage_safety": 0.3}
    assert assessment["scope"]["backend_source_of_truth"] is True


def test_unified_assessment_exposes_not_evaluable_without_fake_score():
    payload = _payload()
    payload["metrics"] = [
        unavailable_metric(item["metric_code"], "missing", source="fixture")
        if item["metric_code"] == "door_width" else item
        for item in payload["metrics"]
    ]
    assessment = build_risk_assessment(payload)
    assert assessment["overall"]["status"] == "insufficient_data"
    assert assessment["overall"]["score"] is None
    assert {item["metric_code"] for item in assessment["not_evaluable"]} == {"door_width"}
    assert all(item["risk_level"] is None for item in assessment["not_evaluable"])


def test_assessment_file_is_serializable_and_preserves_metric_input(tmp_path):
    source = tmp_path / "spatial_metrics.json"
    source.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "risk_assessment.json"
    assessment = build_risk_assessment_file(source, output)
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == "1.0"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert assessment["metric_input"] == {
        "artifact": "spatial_metrics.json", "sha256": before, "input_modified": False,
    }


def test_assessment_file_rejects_non_json_input(tmp_path):
    source = tmp_path / "scene.ply"
    source.write_text("ply")
    with pytest.raises(ValueError, match="must be a JSON"):
        build_risk_assessment_file(source, tmp_path / "risk_assessment.json")


@pytest.mark.parametrize("entrypoint", ["evaluate", "assessment"])
@pytest.mark.parametrize(
    "code,updates,missing_field,reason",
    [
        ("door_width", {"value": float("nan")}, None, "metric_value_non_finite"),
        ("door_width", {"value": float("inf")}, None, "metric_value_non_finite"),
        ("activity_area", {"value": -float("inf")}, None, "metric_value_non_finite"),
        ("door_width", {"value": True}, None, "metric_numeric_required"),
        ("entrance_space", {"value": False}, None, "metric_numeric_required"),
        ("door_width", {"value": "0.9"}, None, "metric_numeric_required"),
        ("door_width", {"value": -0.1}, None, "metric_value_out_of_range"),
        ("crowding", {"value": 1.1}, None, "metric_value_out_of_range"),
        ("crowding", {"value": -0.1}, None, "metric_value_out_of_range"),
        ("path_continuity", {"value": "true"}, None, "metric_boolean_required"),
        ("path_obstruction", {"value": "false"}, None, "metric_boolean_required"),
        ("path_continuity", {"value": 1}, None, "metric_boolean_required"),
        ("path_obstruction", {"value": 0}, None, "metric_boolean_required"),
        ("main_activity_area_safety", {"value": "False"}, None, "metric_boolean_required"),
        ("door_width", {"value": None}, None, "metric_value_missing"),
        ("door_width", {}, "value", "metric_value_missing"),
        ("door_width", {}, "status", "invalid_metric_status"),
        ("door_width", {"status": "safe"}, None, "invalid_metric_status"),
        ("door_width", {"status": None}, None, "invalid_metric_status"),
        ("door_width", {"unit": "cm"}, None, "metric_unit_mismatch"),
        ("door_width", {"category": "layout"}, None, "metric_category_mismatch"),
        ("door_width", {"confidence": float("nan")}, None, "invalid_metric_confidence"),
        ("door_width", {"confidence": float("inf")}, None, "invalid_metric_confidence"),
        ("door_width", {"confidence": True}, None, "invalid_metric_confidence"),
        ("door_width", {"confidence": "high"}, None, "invalid_metric_confidence"),
        ("door_width", {"confidence": 1.1}, None, "invalid_metric_confidence"),
    ],
)
def test_external_metric_validation_prevents_false_safe_results(entrypoint, code, updates, missing_field, reason):
    payload = _payload()
    for metric in payload["metrics"]:
        metric["confidence"] = 0.8
    target = next(item for item in payload["metrics"] if item["metric_code"] == code)
    target.update(updates)
    if missing_field:
        target.pop(missing_field)
    # Stored coverage is not authoritative after external evidence is validated.
    payload["coverage"] = {
        "evaluable_count": 15, "not_evaluable_count": 0, "total_count": 15, "percent": 100.0,
    }
    before = json.dumps(payload, sort_keys=True)

    result = evaluate_formal_metrics(payload) if entrypoint == "evaluate" else build_risk_assessment(payload)
    risks = result if entrypoint == "evaluate" else result["risks"]
    risk = next(item for item in risks if item["metric_code"] == code)
    assert risk["assessment_status"] == "not_evaluable"
    assert risk["measured_value"] is None
    assert risk["risk_level"] is None
    assert risk["reason"] == reason
    assert len([item for item in risks if item["assessment_status"] == "evaluated"]) == 14
    if entrypoint == "assessment":
        metric = next(item for item in result["metrics"] if item["metric_code"] == code)
        assert metric["status"] == "not_evaluable"
        assert metric["value"] is None
        assert metric["reason"] == reason
        assert result["confidence"]["assessment_coverage"] == {
            "evaluated_count": 14, "not_evaluable_count": 1, "total_count": 15, "percent": 93.3,
        }
        assert result["confidence"]["metric_coverage"]["evaluable_count"] == 14
        assert result["confidence"]["metric_coverage"]["percent"] == 93.3
        assert result["confidence"]["confidence_sample_count"] == 14
        assert result["confidence"]["evidence_confidence"] == 0.8
    assert json.dumps(payload, sort_keys=True) == before
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("confidence_field", ["missing", "null"])
def test_missing_confidence_does_not_invalidate_a_real_measurement(confidence_field):
    payload = _payload()
    if confidence_field == "missing":
        for metric in payload["metrics"]:
            metric.pop("confidence")
    assessment = build_risk_assessment(payload)
    assert assessment["overall"]["score"] == 100.0
    assert assessment["confidence"]["assessment_coverage"]["evaluated_count"] == 15
    assert assessment["confidence"]["evidence_confidence"] is None


@pytest.mark.parametrize("code", ["door_width", "main_passage_width", "furniture_spacing", "bed_surrounding_space"])
def test_real_zero_distance_remains_a_high_risk_measurement(code):
    assessment = build_risk_assessment(_payload({code: 0.0}))
    risk = next(item for item in assessment["risks"] if item["metric_code"] == code)
    assert risk["assessment_status"] == "evaluated"
    assert risk["measured_value"] == 0.0
    assert risk["risk_level"] == "high"


@pytest.mark.parametrize(
    "code,value,level",
    [
        ("path_continuity", True, "low"),
        ("path_continuity", False, "high"),
        ("path_obstruction", False, "low"),
        ("path_obstruction", True, "high"),
        ("main_activity_area_safety", True, "low"),
        ("main_activity_area_safety", False, "high"),
    ],
)
def test_true_boolean_observations_keep_their_existing_rule_meaning(code, value, level):
    assessment = build_risk_assessment(_payload({code: value}))
    risk = next(item for item in assessment["risks"] if item["metric_code"] == code)
    assert risk["assessment_status"] == "evaluated"
    assert risk["measured_value"] is value
    assert risk["risk_level"] == level


def test_truncated_catalog_cannot_shrink_coverage_denominator_and_invent_a_score():
    core = {"main_passage_width", "door_width", "path_continuity", "furniture_spacing", "bed_surrounding_space"}
    payload = _payload()
    payload["metrics"] = [item for item in payload["metrics"] if item["metric_code"] in core]
    before = json.dumps(payload, sort_keys=True)
    assessment = build_risk_assessment(payload)
    assert len(assessment["metrics"]) == 15
    assert len(assessment["risks"]) == 15
    assert assessment["confidence"]["assessment_coverage"] == {
        "evaluated_count": 5, "not_evaluable_count": 10, "total_count": 15, "percent": 33.3,
    }
    assert assessment["overall"]["score"] is None
    assert assessment["overall"]["status"] == "insufficient_data"
    assert {item["reason"] for item in assessment["not_evaluable"]} == {"metric_missing_from_payload"}
    assert all(item["risk_level"] is None for item in assessment["not_evaluable"])
    assert json.dumps(payload, sort_keys=True) == before


@pytest.mark.parametrize("payload", [{}, {"metrics": []}, {"metrics": None}])
def test_absent_metric_catalog_is_unknown_and_never_safe(payload):
    assessment = build_risk_assessment(payload)
    assert assessment["overall"]["score"] is None
    assert assessment["overall"]["status"] == "insufficient_data"
    assert assessment["confidence"]["assessment_coverage"] == {
        "evaluated_count": 0, "not_evaluable_count": 15, "total_count": 15, "percent": 0.0,
    }
    assert assessment["top_risks"] == []
    assert all(item["risk_level"] is None for item in assessment["risks"])
    assert all(item["score"] is None for item in assessment["category_scores"].values())
    json.dumps(assessment, allow_nan=False)


@pytest.mark.parametrize("malformation", ["duplicate", "unknown"])
def test_unified_assessment_rejects_ambiguous_metric_catalog(malformation):
    payload = _payload()
    if malformation == "duplicate":
        payload["metrics"].append(dict(payload["metrics"][0]))
    else:
        payload["metrics"].append(payload["metrics"][0] | {"metric_code": "invented_metric"})
    before = json.dumps(payload, sort_keys=True)
    with pytest.raises(ValueError, match="duplicate metric codes|unknown formal metric codes"):
        build_risk_assessment(payload)
    assert json.dumps(payload, sort_keys=True) == before
