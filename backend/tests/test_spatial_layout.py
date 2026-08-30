from pipeline.spatial_layout import extract_furniture_spacing_metric


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
