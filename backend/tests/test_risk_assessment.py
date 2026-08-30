import pytest

from pipeline.risk_assessment import RiskResult, risk_result


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
