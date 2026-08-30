from pipeline.spatial_layout import extract_furniture_spacing_metric, extract_wall_clearance_metrics


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
