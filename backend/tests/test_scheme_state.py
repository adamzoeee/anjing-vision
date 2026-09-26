import json
from pathlib import Path

import pytest

from pipeline.scheme_assessment import assess_scheme, build_scheme, diff_assessments
from pipeline.scheme_state import apply_edits, overlap_depth, _rect_of_measurement

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


def test_rotate_90_swaps_footprint():
    m, s = _measurements(), _structure()
    rotated = build_scheme(m, s, [{"op": "rotate", "target": "desk_001", "quarter_turns": 1}])
    desk = next(x for x in rotated["layout_objects"] if x["id"] == "desk_001")
    assert desk["size"][0] == pytest.approx(0.5)
    assert desk["size"][1] == pytest.approx(1.0)


def test_rotate_180_keeps_footprint_and_270_swaps():
    m, s = _measurements(), _structure()
    half = build_scheme(m, s, [{"op": "rotate", "target": "desk_001", "quarter_turns": 2}])
    desk_m = next(x for x in half["measurements"]["objects"] if x["instance_id"] == "desk_001")
    assert desk_m["length_m"] == pytest.approx(1.0)
    assert desk_m["width_m"] == pytest.approx(0.5)
    assert desk_m["rotation_z_deg"] == pytest.approx(180.0)
    desk = next(x for x in half["layout_objects"] if x["id"] == "desk_001")
    assert desk["size"][0] == pytest.approx(1.0)
    assert desk["size"][1] == pytest.approx(0.5)

    three = build_scheme(m, s, [{"op": "rotate", "target": "desk_001", "quarter_turns": 3}])
    desk_m = next(x for x in three["measurements"]["objects"] if x["instance_id"] == "desk_001")
    assert desk_m["length_m"] == pytest.approx(0.5)
    assert desk_m["width_m"] == pytest.approx(1.0)
    assert desk_m["rotation_z_deg"] == pytest.approx(270.0)
    desk = next(x for x in three["layout_objects"] if x["id"] == "desk_001")
    assert desk["size"][0] == pytest.approx(0.5)
    assert desk["size"][1] == pytest.approx(1.0)


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
