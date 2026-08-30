from pipeline.spatial_paths import normalize_paths


def test_primary_path_is_normalized_to_formal_schema():
    route = {
        "id": "door_to_bed", "from": "door_01", "to": "bed_001",
        "path_exists": True, "path_blocked": False, "path_length_m": 1.36,
        "minimum_clear_width_m": 0.48, "narrowest_point_xy": [1.8, 0.64],
    }
    paths = normalize_paths({"primary_route": route}, {"passages": [route]})
    path = paths[0]
    assert set(path) == {
        "path_id", "start", "target", "status", "length_m", "continuous",
        "detour", "obstructed", "bottleneck", "confidence", "reason",
    }
    assert path["status"] == "complete"
    assert path["continuous"] is True
    assert path["obstructed"] is False
    assert path["bottleneck"] == {"width_m": 0.48, "position_xy": [1.8, 0.64]}


def test_missing_activity_anchor_is_not_replaced_with_room_center():
    paths = normalize_paths(
        {"primary_route": {"id": "door_to_bed", "from": "door_01", "path_exists": True}},
        {"room": {"floor_polygon": [[0, 0], [4, 0], [4, 3], [0, 3]]}},
    )
    activity = next(item for item in paths if item["path_id"] == "entrance_to_activity")
    assert activity["status"] == "not_evaluable"
    assert activity["target"] is None
    assert activity["reason"] == "explicit_activity_anchor_missing"


def test_blocked_path_does_not_publish_length_or_fake_continuity():
    paths = normalize_paths(
        {"primary_route": {
            "id": "door_to_bed", "from": "door_01", "to": "bed_001",
            "path_exists": False, "path_blocked": True, "path_length_m": 3.0,
        }},
        {},
    )
    path = paths[0]
    assert path["status"] == "blocked"
    assert path["length_m"] is None
    assert path["continuous"] is False
    assert path["obstructed"] is True
