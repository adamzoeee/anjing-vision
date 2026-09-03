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
