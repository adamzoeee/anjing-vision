from pipeline.spatial_layout import (
    extract_bedside_clearance_metric,
    extract_bed_surrounding_space_metric,
    extract_activity_area_metric,
    extract_crowding_metric,
    extract_furniture_spacing_metric,
    extract_wall_clearance_metrics,
)


def test_furniture_spacing_selects_smallest_structured_clearance():
    metric = extract_furniture_spacing_metric({
        "furniture_clearances": [
            {"between": ["bed_001", "table_001"], "clearance_m": 0.3},
            {
                "between": ["desk_001", "bookshelf_001"],
                "between_labels": ["书桌一", "书架一"],
                "clearance_m": 0.04,
            },
        ],
    })
    assert metric["value"] == 0.04
    assert metric["position"] == {
        "object_ids": ["desk_001", "bookshelf_001"],
        "labels": ["书桌一", "书架一"],
    }


def test_furniture_spacing_missing_is_not_safe():
    metric = extract_furniture_spacing_metric({"furniture_clearances": []})
    assert metric["status"] == "not_evaluable"
    assert metric["value"] is None
    assert metric["reason"] == "furniture_clearance_unavailable"


def test_wall_clearances_use_rotated_structured_footprints():
    foundation = {
        "room": {"floor_polygon": [[0, 0], [4, 0], [4, 3], [0, 3]]},
        "furniture": [
            {
                "id": "bed_001", "type": "bed", "position_xyz": [2, 2, 0.3],
                "length_m": 2.0, "width_m": 1.0, "rotation_z_deg": 0,
                "confidence": "high",
            },
            {
                "id": "desk_001", "type": "desk", "position_xyz": [0.6, 1, 0.4],
                "length_m": 0.8, "width_m": 0.5, "rotation_z_deg": 0,
            },
        ],
    }
    metrics = {item["metric_code"]: item for item in extract_wall_clearance_metrics(foundation)}
    assert metrics["wall_furniture_clearance"]["value"] == 0.2
    assert metrics["wall_furniture_clearance"]["position"]["object_id"] == "desk_001"
    assert metrics["bed_wall_distance"]["value"] == 0.5
    assert metrics["bed_wall_distance"]["confidence"] == 0.9


def test_missing_floor_or_bed_does_not_create_zero_clearance():
    metrics = {item["metric_code"]: item for item in extract_wall_clearance_metrics({
        "room": {}, "furniture": [{"id": "desk_001", "type": "desk"}],
    })}
    assert metrics["wall_furniture_clearance"]["status"] == "not_evaluable"
    assert metrics["bed_wall_distance"]["status"] == "not_evaluable"


def test_bedside_clearance_selects_nearest_non_attached_furniture():
    passage = {"furniture_clearances": [
        {"between": ["bed_001", "table_001"], "clearance_m": 0.3},
        {"between": ["bed_001", "wardrobe_001"], "clearance_m": 0.62},
        {"between": ["desk_001", "wardrobe_001"], "clearance_m": 0.1},
    ]}
    foundation = {"furniture": [{"id": "bed_001", "type": "bed"}]}
    metric = extract_bedside_clearance_metric(passage, foundation)
    assert metric["value"] == 0.3
    assert metric["position"] == {"object_ids": ["bed_001", "table_001"]}


def test_bedside_clearance_requires_bed_and_relationship_evidence():
    assert extract_bedside_clearance_metric({}, {"furniture": []})["reason"] == (
        "verified_bed_geometry_unavailable"
    )
    metric = extract_bedside_clearance_metric({}, {
        "furniture": [{"id": "bed_001", "type": "bed"}],
    })
    assert metric["status"] == "not_evaluable"
    assert metric["reason"] == "bedside_clearance_unavailable"


def test_activity_area_requires_explicit_anchor():
    metric = extract_activity_area_metric({
        "room": {"area_m2": 12.0},
        "furniture": [{"id": "bed_001", "type": "bed", "length_m": 2, "width_m": 1.5}],
    })
    assert metric["status"] == "not_evaluable"
    assert metric["reason"] == "explicit_activity_anchor_missing"

    measured = extract_activity_area_metric({"furniture": [{
        "id": "activity_01", "type": "activity_area", "length_m": 2.0,
        "width_m": 1.5, "confidence": "medium", "position_xyz": [2, 2, 0],
    }]})
    assert measured["value"] == 3.0
    assert measured["position"]["object_id"] == "activity_01"


def test_crowding_uses_room_and_verified_furniture_footprints():
    metric = extract_crowding_metric({
        "room": {"area_m2": 12.0},
        "furniture": [
            {"length_m": 2.0, "width_m": 1.5},
            {"length_m": 1.0, "width_m": 0.6},
        ],
    })
    assert metric["value"] == 0.3


def test_invalid_crowding_evidence_is_not_evaluable():
    assert extract_crowding_metric({"room": {}})["status"] == "not_evaluable"
    metric = extract_crowding_metric({
        "room": {"area_m2": 1.0},
        "furniture": [{"length_m": 2.0, "width_m": 2.0}],
    })
    assert metric["reason"] == "furniture_footprint_area_exceeds_room_area"


def test_bed_surrounding_space_uses_most_constrained_observed_boundary():
    foundation = {
        "room": {"floor_polygon": [[0, 0], [4, 0], [4, 3], [0, 3]]},
        "furniture": [{
            "id": "bed_001", "type": "bed", "position_xyz": [2, 2, 0.3],
            "length_m": 2, "width_m": 1, "confidence": "high",
        }],
    }
    passage = {"furniture_clearances": [{
        "between": ["bed_001", "table_001"], "clearance_m": 0.3,
    }]}
    metric = extract_bed_surrounding_space_metric(passage, foundation)
    assert metric["value"] == 0.3
    assert metric["unit"] == "m"
    assert metric["position"]["boundary"] == "furniture"


def test_bed_surrounding_space_without_bed_is_not_evaluable():
    metric = extract_bed_surrounding_space_metric({}, {"furniture": []})
    assert metric["status"] == "not_evaluable"
    assert metric["reason"] == "verified_bed_geometry_unavailable"
