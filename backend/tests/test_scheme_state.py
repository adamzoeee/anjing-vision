import json
from pathlib import Path

import pytest

from pipeline.scheme_assessment import assess_scheme, build_scheme, diff_assessments
from pipeline.scheme_state import apply_edits, overlap_depth, _corners, _rect_of_measurement

SCAN46 = Path(__file__).resolve().parents[1] / "data" / "work" / "46" / "postprocess"


def _measurements():
    return {
        "room": {"length_m": 4.0, "width_m": 4.0, "height_m": 2.7,
                 "measurement_status": "verified", "confidence": "medium"},
        "openings": [
            {"id": "door_01", "type": "door", "center": [0.6, 0.0, 1.05],
             "width_m": 0.85, "height_m": 2.1, "rotation_z_deg": 0.0,
             "measurement_status": "verified"},
        ],
        "objects": [
            {"id": "bed_001", "instance_id": "bed_001", "type": "bed",
             "center": [1.0, 3.0, 0.3], "length_m": 2.0, "width_m": 1.2, "height_m": 0.6,
             "rotation_z_deg": 0.0, "measurement_status": "verified", "confidence": "medium"},
            {"id": "desk_001", "instance_id": "desk_001", "type": "desk",
             "center": [1.2, 2.0, 0.4], "length_m": 1.0, "width_m": 0.5, "height_m": 0.8,
             "rotation_z_deg": 0.0, "measurement_status": "verified", "confidence": "medium"},
        ],
    }


def _structure():
    return {
        "room": {"bounds_xy": {"min": [0.0, 0.0], "max": [4.0, 4.0]}, "height_m": 2.7},
        "walls": [], "geometric_obstacles": [],
        "doors": [{"instance_id": "door_01", "center": [0.6, 0.0, 1.05],
                   "size": [0.85, 0.12, 2.1], "rotation_z_deg": 0.0}],
        "semantic_instances": [
            {"instance_id": "bed_001", "label": "bed", "center": [1.0, 3.0, 0.3], "size": [2.0, 1.2, 0.6]},
            {"instance_id": "desk_001", "label": "desk", "center": [1.2, 2.0, 0.4], "size": [1.0, 0.5, 0.8]},
        ],
    }


def test_overlap_depth_of_two_rects():
    a = [{"center": [0.0, 0.0, 0.0], "length_m": 1.0, "width_m": 1.0, "rotation_z_deg": 0.0}]
    b = [{"center": [0.7, 0.0, 0.0], "length_m": 1.0, "width_m": 1.0, "rotation_z_deg": 0.0}]
    assert overlap_depth(_rect_of_measurement(a[0]), _rect_of_measurement(b[0])) == pytest.approx(0.3, abs=1e-6)


def test_empty_edits_reproduce_official_assessment():
    m, s = _measurements(), _structure()
    direct = assess_scheme(m, s)
    result = build_scheme(m, s, [])
    assert result["overall"] == direct["overall"]
    assert [x["metric_code"] for x in result["metrics"]] == [x["metric_code"] for x in direct["metrics"]]
    assert result["scope"]["simulation"] is True
    assert result["scope"]["raw_media_accessed"] is False


def test_move_changes_metric_and_keeps_inputs_untouched():
    m, s = _measurements(), _structure()
    before = build_scheme(m, s, [])
    after = build_scheme(m, s, [{"op": "move", "target": "desk_001", "dx": 0.0, "dy": -1.0}])
    spacing_before = next(x["value"] for x in before["metrics"] if x["metric_code"] == "furniture_spacing")
    spacing_after = next(x["value"] for x in after["metrics"] if x["metric_code"] == "furniture_spacing")
    assert spacing_after != spacing_before
    desk = next(x for x in m["objects"] if x.get("instance_id") == "desk_001")
    assert desk["center"] == [1.2, 2.0, 0.4]  # 原输入未被修改


def test_overlap_is_reported_and_rejected():
    m, s = _measurements(), _structure()
    baseline = build_scheme(m, s, [])
    result = build_scheme(m, s, [{"op": "move", "target": "desk_001", "dx": 0.2, "dy": 0.9}])
    assert result["conflicts"], "重叠必须被报告"
    assert result["conflicts"][0]["type"] == "overlap"
    assert result["overall"]["score"] == baseline["overall"]["score"]


def test_out_of_room_is_reported_and_rejected():
    m, s = _measurements(), _structure()
    result = build_scheme(m, s, [{"op": "move", "target": "bed_001", "dx": 0.0, "dy": 2.0}])
    assert result["conflicts"][0]["type"] == "out_of_room"


def test_remove_and_add_participate_in_scoring():
    m, s = _measurements(), _structure()
    before = build_scheme(m, s, [])
    removed = build_scheme(m, s, [{"op": "remove", "target": "desk_001"}])
    added = build_scheme(m, s, [{"op": "add", "label": "箱子", "size": [0.6, 0.4, 0.5], "center": [3.0, 3.0, 0.25]}])
    crowd_before = next(x["value"] for x in before["metrics"] if x["metric_code"] == "crowding")
    crowd_removed = next(x["value"] for x in removed["metrics"] if x["metric_code"] == "crowding")
    crowd_added = next(x["value"] for x in added["metrics"] if x["metric_code"] == "crowding")
    assert crowd_removed < crowd_before < crowd_added
    assert added["assumed_ids"] == ["assumed_1"]
    assert added["scope"]["contains_assumed_objects"] is True


@pytest.mark.parametrize("turns,expected", [
    (1, [(1.75, 1.0), (1.75, 2.0), (2.25, 1.0), (2.25, 2.0)]),
    (2, [(1.5, 1.25), (1.5, 1.75), (2.5, 1.25), (2.5, 1.75)]),
    (3, [(1.75, 1.0), (1.75, 2.0), (2.25, 1.0), (2.25, 2.0)]),
])
def test_rotations_change_physical_corners_and_mirror_layout(turns, expected):
    m, s = _measurements(), _structure()
    m["objects"][1]["center"] = [2.0, 1.5, 0.4]
    s["semantic_instances"][1]["center"] = [2.0, 1.5, 0.4]
    rotated = build_scheme(m, s, [{"op": "rotate", "target": "desk_001", "quarter_turns": turns}])
    assert rotated["conflicts"] == []
    desk_m = rotated["measurements"]["objects"][1]
    desk_s = next(item for item in rotated["layout_objects"] if item["id"] == "desk_001")
    for corners in (_rect_of_measurement(desk_m), _corners(desk_s["center"], *desk_s["size"][:2], desk_s["rotation_z_deg"])):
        actual = sorted((round(x, 6), round(y, 6)) for x, y in corners)
        assert actual == expected
    assert desk_m["length_m"] == 1.0 and desk_m["width_m"] == 0.5
    assert desk_s["size"] == [1.0, 0.5, 0.8]
    assert desk_m["rotation_z_deg"] == desk_s["rotation_z_deg"] == 90 * turns


def test_rotation_mirrors_missing_structure_object():
    m, s = _measurements(), _structure()
    m["objects"][1]["center"] = [2.0, 1.5, 0.4]
    s["semantic_instances"] = s["semantic_instances"][:1]
    rotated = build_scheme(m, s, [{"op": "rotate", "target": "desk_001", "quarter_turns": 1}])
    assert rotated["conflicts"] == []
    desk = next(item for item in rotated["layout_objects"] if item["id"] == "desk_001")
    assert desk["size"] == [1.0, 0.5, 0.8]
    assert desk["rotation_z_deg"] == 90
    assert desk["center"] == [2.0, 1.5, 0.4]


def test_rotation_mirrors_legacy_objects_only_structure():
    m, s = _measurements(), _structure()
    m["objects"][1]["center"] = [2.0, 1.5, 0.4]
    s["objects"] = s.pop("semantic_instances")
    s["objects"][1]["center"] = [2.0, 1.5, 0.4]

    rotated = build_scheme(m, s, [{"op": "rotate", "target": "desk_001", "quarter_turns": 1}])

    assert rotated["conflicts"] == []
    assert "semantic_instances" not in rotated["structure"]
    assert len(rotated["layout_after"]["objects"]) == 2
    desk = next(item for item in rotated["layout_after"]["objects"] if item["id"] == "desk_001")
    assert desk["size"] == [1.0, 0.5, 0.8]
    assert desk["rotation_z_deg"] == 90


def test_rotation_preserves_legacy_structure_local_axes():
    m, s = _measurements(), _structure()
    m["objects"][1]["center"] = [2.0, 1.5, 0.4]
    s["objects"] = s.pop("semantic_instances")
    s["objects"][1].update(center=[2.0, 1.5, 0.4], size=[0.5, 1.0, 0.8])
    before = sorted((round(x, 6), round(y, 6)) for x, y in
                    _corners([2.0, 1.5, 0.4], 0.5, 1.0, 0))

    rotated = build_scheme(m, s, [{"op": "rotate", "target": "desk_001", "quarter_turns": 2}])

    assert rotated["conflicts"] == []
    desk = next(item for item in rotated["layout_after"]["objects"] if item["id"] == "desk_001")
    assert desk["size"] == [0.5, 1.0, 0.8]
    assert desk["rotation_z_deg"] == 180
    assert sorted((round(x, 6), round(y, 6)) for x, y in
                  _corners(desk["center"], *desk["size"][:2], desk["rotation_z_deg"])) == before


def test_legacy_objects_move_then_rotate_keeps_other_furniture_visible():
    m, s = _measurements(), _structure()
    m["objects"][1]["center"] = [2.0, 1.5, 0.4]
    s["objects"] = s.pop("semantic_instances")
    s["objects"][1]["center"] = [2.0, 1.5, 0.4]

    result = build_scheme(m, s, [
        {"op": "move", "target": "desk_001", "dx": 0.1, "dy": 0.0},
        {"op": "rotate", "target": "desk_001", "quarter_turns": 1},
    ])

    assert result["conflicts"] == []
    assert len(result["applied_edits"]) == 2
    assert "semantic_instances" not in result["structure"]
    assert {item["id"] for item in result["layout_after"]["objects"]} == {"bed_001", "desk_001"}
    desk = next(item for item in result["layout_after"]["objects"] if item["id"] == "desk_001")
    assert desk["center"] == [2.1, 1.5, 0.4]
    assert desk["rotation_z_deg"] == 90


def test_rotation_rejects_real_new_overlap():
    m, s = _measurements(), _structure()
    rotated = apply_edits(m, s, [{"op": "rotate", "target": "desk_001", "quarter_turns": 1}])
    assert rotated["conflicts"][0]["type"] == "overlap"
    assert rotated["applied_edits"] == []


def test_move_mirrors_into_structure_when_missing():
    m = _measurements()
    s = {"room": {"bounds_xy": {"min": [0.0, 0.0], "max": [4.0, 4.0]}, "height_m": 2.7},
         "walls": [], "geometric_obstacles": [], "doors": [],
         "semantic_instances": []}
    result = build_scheme(m, s, [{"op": "move", "target": "desk_001", "dx": 0.0, "dy": -1.0}])
    mirrored = next(x for x in result["structure"]["semantic_instances"] if x["instance_id"] == "desk_001")
    assert mirrored["center"][0] == pytest.approx(1.2)
    assert mirrored["center"][1] == pytest.approx(1.0)
    assert mirrored["size"][0] == pytest.approx(1.0)
    assert mirrored["size"][1] == pytest.approx(0.5)
    assert mirrored["label"] == "desk"


def test_resize_door_changes_door_width_metric():
    m, s = _measurements(), _structure()
    widened = build_scheme(m, s, [{"op": "resize_door", "width_m": 0.90}])
    value = next(x["value"] for x in widened["metrics"] if x["metric_code"] == "door_width")
    assert value == pytest.approx(0.90, abs=1e-6)


def test_diff_assessments_reports_changes():
    m, s = _measurements(), _structure()
    before = build_scheme(m, s, [])
    after = build_scheme(m, s, [{"op": "remove", "target": "desk_001"}])
    diff = diff_assessments(before, after)
    assert "before_score" in diff["comparison"] and "after_score" in diff["comparison"]
    assert any(x["metric_code"] == "crowding" for x in diff["metric_changes"])


@pytest.mark.skipif(not (SCAN46 / "spatial_metrics.json").is_file(), reason="scan 46 artifacts not present")
def test_golden_scan46_matches_official_metrics():
    m = json.loads((SCAN46 / "measurements.json").read_text(encoding="utf-8"))
    s = json.loads((SCAN46 / "structure_calibrated.json").read_text(encoding="utf-8"))
    official = json.loads((SCAN46 / "spatial_metrics.json").read_text(encoding="utf-8"))
    official_risks = json.loads((SCAN46 / "risk_assessment.json").read_text(encoding="utf-8"))
    result = build_scheme(m, s, [])
    assert {x["metric_code"]: x["value"] for x in result["metrics"]} == \
           {x["metric_code"]: x["value"] for x in official["metrics"]}
    assert result["overall"]["score"] == official_risks["overall"]["score"]

def test_resize_door_accepts_plausible_width():
    m, s = _measurements(), _structure()
    widened = build_scheme(m, s, [{"op": "resize_door", "width_m": 0.90}])
    assert widened["conflicts"] == []
    value = next(x["value"] for x in widened["metrics"] if x["metric_code"] == "door_width")
    assert value == pytest.approx(0.90, abs=1e-6)


@pytest.mark.parametrize("width", [5.0, -1.0, 0.2, 1.8, 0.0, "abc"])
def test_resize_door_rejects_out_of_range_width(width):
    m, s = _measurements(), _structure()
    result = build_scheme(m, s, [{"op": "resize_door", "width_m": width}])
    assert result["conflicts"] and result["conflicts"][0]["type"] == "invalid_door_width"
    assert result["applied_edits"] == []
    # 越界宽度不得改动原始门洞
    assert m["openings"][0]["width_m"] == pytest.approx(0.85, abs=1e-6)
    value = next(x["value"] for x in result["metrics"] if x["metric_code"] == "door_width")
    assert value == pytest.approx(0.85, abs=1e-6)


def test_resize_door_without_width_defaults_to_point_nine():
    m, s = _measurements(), _structure()
    result = build_scheme(m, s, [{"op": "resize_door"}])
    assert result["conflicts"] == []
    value = next(x["value"] for x in result["metrics"] if x["metric_code"] == "door_width")
    assert value == pytest.approx(0.90, abs=1e-6)


@pytest.mark.parametrize("center,angle", [([0.6, 0, 1.05], 0), ([0, 0.6, 1.05], 90),
                                          ([3.4, 4, 1.05], 180), ([4, 3.4, 1.05], 270)])
def test_resize_door_rejects_opening_endpoints_beyond_room(center, angle):
    m, s = _measurements(), _structure()
    m["openings"][0].update(center=center, rotation_z_deg=angle)
    s["doors"][0].update(center=center, rotation_z_deg=angle)
    result = apply_edits(m, s, [{"op": "resize_door", "width_m": 1.5}])
    assert result["conflicts"][0]["type"] == "door_out_of_room"
    assert result["applied_edits"] == []
    assert result["measurements"]["openings"][0]["width_m"] == 0.85
    assert result["structure"]["doors"][0]["size"][0] == 0.85


@pytest.mark.parametrize("angle", [0, 45, 90])
@pytest.mark.parametrize("linked", [False, True])
def test_resize_door_respects_containing_wall_segment(angle, linked):
    m, s = _measurements(), _structure()
    wall = {"id": "short_wall", "center": [2, 2, 1.35], "size": [1.2, 0.12, 2.7], "rotation_z_deg": angle}
    s["walls"] = [wall]
    m["openings"][0].update(center=[2, 2, 1.05], rotation_z_deg=angle)
    s["doors"][0].update(center=[2, 2, 1.05], rotation_z_deg=angle)
    if linked:
        s["doors"][0]["wall_id"] = "short_wall"
    result = apply_edits(m, s, [{"op": "resize_door", "width_m": 1.4}])
    assert result["conflicts"][0]["type"] == "door_out_of_wall"
    assert result["applied_edits"] == []


@pytest.mark.parametrize("center", [[1.2, 0.0, 1.05], [2.0, 2.0, 1.05]])
def test_resize_door_rejects_opening_without_containing_wall(center):
    m, s = _measurements(), _structure()
    s["walls"] = [{"id": "partial_wall", "center": [0.4, 0.0, 1.35],
                   "size": [0.8, 0.12, 2.7], "rotation_z_deg": 0}]
    m["openings"][0]["center"] = center
    s["doors"][0]["center"] = center

    result = apply_edits(m, s, [{"op": "resize_door", "width_m": 1.0}])

    assert result["conflicts"][0]["type"] == "door_out_of_wall"
    assert result["applied_edits"] == []
    assert result["measurements"]["openings"][0]["width_m"] == 0.85
    assert result["structure"]["doors"][0]["size"][0] == 0.85


@pytest.mark.parametrize("center,angle,wall", [
    ([0.6, 0, 1.05], 0, None), ([0, 0.6, 1.05], 90, None),
    ([2, 2, 1.05], 45, {"id": 0, "center": [2, 2, 1.35], "size": [1.2, 0.12, 2.7], "rotation_z_deg": 45}),
])
def test_resize_door_can_touch_room_or_wall_endpoint(center, angle, wall):
    m, s = _measurements(), _structure()
    m["openings"][0].update(center=center, rotation_z_deg=angle)
    s["doors"][0].update(center=center, rotation_z_deg=angle)
    if wall is not None:
        s["walls"] = [wall]
        s["doors"][0]["wall_id"] = 0
    result = apply_edits(m, s, [{"op": "resize_door", "width_m": 1.2}])
    assert result["conflicts"] == []
    assert len(result["applied_edits"]) == 1


def test_resize_door_checks_all_openings_atomically():
    m, s = _measurements(), _structure()
    m["openings"].append({**m["openings"][0], "id": "door_02", "center": [3.65, 4, 1.05], "width_m": 0.5})
    s["doors"].append({**s["doors"][0], "instance_id": "door_02", "center": [3.65, 4, 1.05], "size": [0.5, 0.12, 2.1]})
    result = apply_edits(m, s, [{"op": "resize_door", "width_m": 1.0}])
    assert result["conflicts"][0]["type"] == "door_out_of_room"
    assert result["applied_edits"] == []
    assert [door["width_m"] for door in result["measurements"]["openings"]] == [0.85, 0.5]
    assert [door["size"][0] for door in result["structure"]["doors"]] == [0.85, 0.5]


def test_resize_door_uses_structure_orientation_when_measurements_omit_it():
    # measurement_builder emits opening centers/widths without angle, while
    # structure_builder's corresponding doors may not have stable IDs.
    m, s = _measurements(), _structure()
    m["openings"][0]["center"] = [0, 0.6, 1.05]
    m["openings"][0].pop("rotation_z_deg")
    s["doors"][0].update(center=[0, 0.6, 1.05], rotation_z_deg=90)
    s["doors"][0].pop("instance_id")
    result = apply_edits(m, s, [{"op": "resize_door", "width_m": 1.0}])
    assert result["conflicts"] == []
    assert result["measurements"]["openings"][0]["width_m"] == 1.0
    assert result["structure"]["doors"][0]["size"][0] == 1.0
