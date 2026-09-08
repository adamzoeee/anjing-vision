from copy import deepcopy

import pytest

from assistant.intent_parser import parse_intent
from pipeline.renovation_compare import compare_assessments
from pipeline.renovation_simulator import simulate
from pipeline.risk_assessment import build_risk_assessment
from pipeline.spatial_metrics import build_metric, build_metric_payload


def _payload():
    values = {
        "main_passage_width": 0.65, "minimum_passage_width": 0.60,
        "door_width": 0.85, "entrance_space": 0.8, "path_length": 4.0,
        "path_continuity": False, "path_obstruction": True,
        "furniture_spacing": 0.2, "wall_furniture_clearance": 0.1,
        "bed_wall_distance": 0.2, "bedside_clearance": 0.3,
        "activity_area": 3.5, "crowding": 0.7,
        "bed_surrounding_space": 0.3, "main_activity_area_safety": False,
    }
    return build_metric_payload([
        build_metric(code, value=value, status="derived", confidence=0.8)
        for code, value in values.items()
    ])


def _mostly_safe_payload(**overrides):
    values = {
        "main_passage_width": 1.0, "minimum_passage_width": 1.0,
        "door_width": 1.0, "entrance_space": 2.0, "path_length": 2.0,
        "path_continuity": True, "path_obstruction": False,
        "furniture_spacing": 0.8, "wall_furniture_clearance": 0.2,
        "bed_wall_distance": 0.2, "bedside_clearance": 0.8,
        "activity_area": 4.0, "crowding": 0.3,
        "bed_surrounding_space": 0.8, "main_activity_area_safety": True,
    }
    values.update(overrides)
    return build_metric_payload([
        build_metric(code, value=value, status="derived", confidence=0.8)
        for code, value in values.items()
    ])


@pytest.mark.parametrize("text,action", [
    ("移除书架", "REMOVE"), ("把床往左移动30厘米", "MOVE"),
    ("在床边放一个60×40×50厘米的箱子", "ADD"),
])
def test_supported_intents(text, action):
    assert parse_intent(text)["action"] == action


def test_unrelated_question_is_rejected():
    with pytest.raises(ValueError):
        parse_intent("今天天气怎么样")


def _structure():
    return {
        "room": {"bounds_xy": {"min": [0, 0], "max": [4, 4]}, "height_m": 2.7},
        "semantic_instances": [
            {"instance_id": "bed_1", "label": "bed", "center": [2.5, 3.0, 0.3], "size": [2.0, 1.2, 0.6]},
            {"instance_id": "desk_1", "label": "desk", "center": [2.0, 1.8, 0.4], "size": [1.2, 0.6, 0.8]},
            {"instance_id": "shelf_1", "label": "bookshelf", "center": [3.4, 2.0, 0.8], "size": [0.5, 1.2, 1.6]},
        ],
        "walls": [], "geometric_obstacles": [],
        "doors": [{"instance_id": "door_1", "center": [0.2, 0.5, 1.0], "size": [0.85, 0.18, 2.0]}],
    }


def test_simulation_uses_memory_copy_and_reports_real_metric_changes():
    payload = _payload()
    original = deepcopy(payload)
    result = simulate(payload, parse_intent("把书架向左移动30厘米"), _structure())
    assert payload == original
    assert result["comparison"]["before_score"] is not None
    assert isinstance(result["metric_changes"], list)


def test_single_multiple_and_all_suggestions_are_accumulated_in_one_simulation():
    payload = _payload()
    all_ids = list(range(1, len(build_risk_assessment(payload)["top_risks"]) + 1))
    for selected in ([1], [1, 2], [1, 2, 3], all_ids):
        intent = {"action": "APPLY_SUGGESTIONS", "suggestion_ids": selected, "raw": str(selected)}
        result = simulate(payload, intent, _structure())
        assert [item["id"] for item in intent["applied_suggestions"]] == selected
        assert result["comparison"]["before_score"] is not None
        assert result["comparison"]["after_score"] is not None


def test_high_risk_improves_more_than_medium_and_all_suggestions_exceed_ninety():
    payload = _mostly_safe_payload(door_width=0.70, crowding=0.50)
    assessment = build_risk_assessment(payload)
    risks = assessment["top_risks"]
    high_id = next(i for i, risk in enumerate(risks, 1) if risk["risk_level"] == "high")
    medium_id = next(i for i, risk in enumerate(risks, 1) if risk["risk_level"] == "medium")

    high = simulate(payload, {
        "action": "APPLY_SUGGESTIONS", "suggestion_ids": [high_id], "raw": "high",
    }, _structure())
    medium = simulate(payload, {
        "action": "APPLY_SUGGESTIONS", "suggestion_ids": [medium_id], "raw": "medium",
    }, _structure())
    all_result = simulate(payload, {
        "action": "APPLY_SUGGESTIONS", "suggestion_ids": [1, 2], "raw": "all",
    }, _structure())

    assert high["comparison"]["score_delta"] > medium["comparison"]["score_delta"] > 0
    assert all_result["comparison"]["after_score"] >= 90.0
