import pytest

from pipeline.spatial_metrics import SpatialMetric, metric_record


def test_spatial_metric_serializes_all_contract_fields():
    metric = SpatialMetric(
        metric_code="door_width",
        name="门净宽",
        value=0.86,
        unit="m",
        status="measured",
        confidence=0.9,
        position={"object_id": "door_01"},
        source={"artifact": "measurements.json", "field": "openings[0].width_m"},
    )

    assert metric.to_dict() == {
        "metric_code": "door_width",
        "name": "门净宽",
        "value": 0.86,
        "unit": "m",
        "status": "measured",
        "confidence": 0.9,
        "position": {"object_id": "door_01"},
        "source": {"artifact": "measurements.json", "field": "openings[0].width_m"},
        "reason": None,
    }


def test_not_evaluable_metric_requires_reason_and_null_value():
    record = metric_record(
        "activity_area",
        "活动区域面积",
        reason="explicit_activity_anchor_missing",
    )
    assert record["status"] == "not_evaluable"
    assert record["value"] is None

    with pytest.raises(ValueError, match="require a reason"):
        metric_record("activity_area", "活动区域面积")
    with pytest.raises(ValueError, match="must not carry a value"):
        metric_record(
            "activity_area", "活动区域面积", value=2.0,
            reason="explicit_activity_anchor_missing",
        )


def test_metric_rejects_invalid_status_or_confidence():
    with pytest.raises(ValueError, match="unsupported metric status"):
        metric_record("door_width", "门净宽", value=0.8, status="safe")
    with pytest.raises(ValueError, match="between 0 and 1"):
        metric_record(
            "door_width", "门净宽", value=0.8, status="measured", confidence=1.2,
        )
