import pytest

from pipeline.spatial_metrics import (
    METRIC_DEFINITION_BY_CODE,
    METRIC_DEFINITIONS,
    SpatialMetric,
    build_metric,
    build_metric_payload,
    confidence_value,
    extract_passage_width_metrics,
    extract_door_width_metric,
    extract_entrance_space_metric,
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
    assert METRIC_DEFINITION_BY_CODE["bed_surrounding_space"]["unit"] == "m"


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


def test_confidence_normalization_handles_labels_numbers_and_missing_values():
    assert confidence_value("high") == 0.9
    assert confidence_value("MEDIUM") == 0.7
    assert confidence_value(1.4) == 1.0
    assert confidence_value(-0.2) == 0.0
    assert confidence_value(None) is None
    assert confidence_value("unsupported", default=0.5) == 0.5


def _complete_unavailable_metric_set():
    return [
        unavailable_metric(code, "fixture_missing", source="fixture")
        for code in METRIC_DEFINITION_BY_CODE
    ]


def test_metric_payload_groups_complete_catalog_and_reports_coverage():
    metrics = _complete_unavailable_metric_set()
    metrics[0] = build_metric(
        metrics[0]["metric_code"], value=0.8, status="derived",
        confidence=0.9, source="fixture",
    )
    payload = build_metric_payload(metrics)
    assert payload["schema_version"] == "1.0"
    assert payload["coverage"] == {
        "evaluable_count": 1,
        "not_evaluable_count": 14,
        "total_count": 15,
        "percent": 6.7,
    }
    assert {item["category"] for item in payload["metrics"]} == {
        "mobility", "layout", "usage_safety",
    }


def test_metric_payload_rejects_missing_or_duplicate_codes():
    metrics = _complete_unavailable_metric_set()
    with pytest.raises(ValueError, match="missing formal metric codes"):
        build_metric_payload(metrics[:-1])
    with pytest.raises(ValueError, match="duplicate metric codes"):
        build_metric_payload([*metrics, metrics[0]])


def test_passage_width_metrics_use_structured_route_and_narrowest_position():
    passage = {
        "status": "ok",
        "primary_route": {
            "id": "door_to_bed", "path_exists": True,
            "minimum_clear_width_m": 0.72,
            "narrowest_point_xy": [1.2, 0.8],
        },
    }
    foundation = {
        "passages": [
            passage["primary_route"],
            {"id": "door_to_chair", "path_exists": True, "minimum_clear_width_m": 0.64},
        ],
    }
    metrics = {item["metric_code"]: item for item in extract_passage_width_metrics(passage, foundation)}
    assert metrics["main_passage_width"]["value"] == 0.72
    assert metrics["minimum_passage_width"]["value"] == 0.64
    assert metrics["main_passage_width"]["position"] == {
        "path_id": "door_to_bed", "point_xy": [1.2, 0.8],
    }


def test_missing_passage_width_is_not_evaluable_not_safe():
    metrics = extract_passage_width_metrics({"status": "blocked", "reason": "no_path"})
    assert len(metrics) == 2
    assert all(item["status"] == "not_evaluable" for item in metrics)
    assert all(item["value"] is None for item in metrics)
    assert {item["reason"] for item in metrics} == {"no_path"}


def test_door_width_uses_verified_route_entrance():
    measurements = {"openings": [
        {
            "id": "door_secondary", "type": "door", "width_m": 1.1,
            "measurement_status": "verified", "confidence": "high",
        },
        {
            "id": "door_01", "type": "door", "width_m": 0.86,
            "measurement_status": "verified", "confidence": "medium",
            "center": [1, 0, 1],
        },
    ]}
    metric = extract_door_width_metric(
        measurements, {"primary_route": {"from": "door_01"}},
    )
    assert metric["value"] == 0.86
    assert metric["confidence"] == 0.7
    assert metric["position"]["object_id"] == "door_01"


def test_unverified_door_width_remains_not_evaluable():
    metric = extract_door_width_metric(
        {"openings": [{
            "id": "door_01", "type": "door", "width_m": 0.5,
            "measurement_status": "low_confidence",
        }]},
        {"primary_route": {"from": "door_01"}},
    )
    assert metric["value"] is None
    assert metric["status"] == "not_evaluable"
    assert metric["reason"] == "door_measurement_not_verified"


def test_entrance_space_uses_door_connected_walkable_area():
    metric = extract_entrance_space_metric({
        "primary_route": {"from": "door_01"},
        "walkable_regions": {"door_connected_area_m2": 2.163},
    })
    assert metric["value"] == 2.163
    assert metric["unit"] == "m²"
    assert metric["position"] == {"object_id": "door_01"}


def test_entrance_space_requires_explicit_door_and_area():
    assert extract_entrance_space_metric({})["status"] == "not_evaluable"
    metric = extract_entrance_space_metric({
        "walkable_regions": {"door_connected_area_m2": 4.0},
    })
    assert metric["reason"] == "entrance_door_missing"
