import pytest

from pipeline.spatial_metrics import (
    METRIC_DEFINITION_BY_CODE,
    METRIC_DEFINITIONS,
    SpatialMetric,
    build_metric,
    metric_record,
    unavailable_metric,
)


def test_formal_metric_catalog_has_all_required_categories_and_codes():
    assert {item[0] for item in METRIC_DEFINITIONS} == {
        "mobility", "layout", "usage_safety",
    }
    assert set(METRIC_DEFINITION_BY_CODE) == {
        "main_passage_width", "minimum_passage_width", "door_width",
        "entrance_space", "path_length", "path_continuity", "path_obstruction",
        "furniture_spacing", "wall_furniture_clearance", "bed_wall_distance",
        "bedside_clearance", "activity_area", "crowding",
        "bed_surrounding_space", "main_activity_area_safety",
    }
    assert METRIC_DEFINITION_BY_CODE["door_width"] == {
        "category": "mobility", "name": "门净宽", "unit": "m",
    }


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


def test_catalog_builder_supplies_category_name_and_unit():
    record = build_metric(
        "minimum_passage_width",
        value=0.72,
        status="derived",
        confidence=0.8,
        source={"artifact": "passage_analysis.json", "field": "primary_route.minimum_clear_width_m"},
    )
    assert record["category"] == "mobility"
    assert record["name"] == "最小通道净宽"
    assert record["unit"] == "m"


def test_unavailable_builder_is_explicit_and_unknown_codes_are_rejected():
    record = unavailable_metric(
        "activity_area", "explicit_activity_anchor_missing",
        source={"artifact": "spatial_foundation.json"},
    )
    assert record["status"] == "not_evaluable"
    assert record["reason"] == "explicit_activity_anchor_missing"

    with pytest.raises(ValueError, match="unknown formal metric code"):
        build_metric("invented_metric", value=1, status="derived")
