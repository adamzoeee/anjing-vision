from pipeline.measurement_builder import build_measurements


def test_references_compute_one_global_scale():
    structure = {
        "room": {"height_m": 2.59, "bounds_xy": {"min": [-1, -1], "max": [2, 1]}},
        "doors": [{"center": [0, -1, .93], "size": [.7, .1, 1.86]}],
        "windows": [],
        "objects": [
            {"label": "bed", "instance_id": "bed_01", "center": [0, 0, .3], "size": [1.72, 1.5, .5]},
            {"label": "desk", "instance_id": "desk_01", "center": [1, 0, .4], "size": [1.1, .5, .8]},
        ],
    }
    result = build_measurements(structure, [
        {"object_type": "door", "dimension": "height", "meters": 2.10},
        {"object_type": "bed", "dimension": "length", "meters": 1.94},
        {"object_type": "table", "dimension": "width", "meters": .45},
    ], validation_keys={("table", "width")})
    assert result["room"]["height_m"] > 2.59
    assert result["openings"][0]["height_m"] > 1.86
    assert result["objects"][0]["length_m"] > 1.72
    assert result["scale"]["global_rescale_applied"] is True


def test_validation_reference_is_not_used_to_compute_scale():
    structure = {
        "room": {"height_m": 2.6, "bounds_xy": {"min": [0, 0], "max": [3, 2]}},
        "doors": [], "windows": [],
        "objects": [{"label": "desk", "instance_id": "desk_01", "size": [1.2, .5, .75]}],
    }
    result = build_measurements(
        structure, [
            {"object_type": "door", "dimension": "height", "meters": 2.1},
            {"object_type": "bed", "dimension": "length", "meters": 2.0},
            {"object_type": "table", "dimension": "width", "meters": .45},
        ],
        validation_keys={("table", "width")},
    )
    assert result["scale"]["status"] == "failed"  # 此合成结构没有门和床，不能靠书桌自标定
    check = result["quality"]["validation"][0]
    assert check["predicted_m"] == .5
    assert round(check["absolute_error_m"], 3) == .05
