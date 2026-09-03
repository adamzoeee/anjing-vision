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


@pytest.mark.parametrize("text,action", [
    ("移除书架", "REMOVE"), ("把床往左移动30厘米", "MOVE"),
    ("把桌子缩小到80厘米", "RESIZE"), ("安装夜灯", "ADD"),
])
def test_supported_intents(text, action):
    assert parse_intent(text)["action"] == action


def test_unrelated_question_is_rejected():
    with pytest.raises(ValueError):
        parse_intent("今天天气怎么样")


def test_simulation_improves_score_without_mutating_source():
    payload = _payload()
    original = deepcopy(payload)
    before = build_risk_assessment(payload)
    result = simulate(payload, parse_intent("移除主要障碍"))
    comparison = compare_assessments(before, result["assessment"])
    assert payload == original
    assert comparison["after_score"] > comparison["before_score"]
    assert result["changes"]


def test_qualitative_add_does_not_invent_score_change():
    payload = _payload()
    before = build_risk_assessment(payload)
    result = simulate(payload, parse_intent("安装夜灯"))
    assert result["changes"] == []
    assert result["assessment"]["overall"]["score"] == before["overall"]["score"]
