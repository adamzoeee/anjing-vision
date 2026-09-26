import pytest

from pipeline import scheme_solver
from pipeline.scheme_assessment import build_scheme
from pipeline.scheme_solver import candidate_edits, solve_metric

_LEVEL_RANK = {None: 0, "low": 0, "medium": 1, "high": 2}


def _level(assessment, metric_code):
    risk = next(r for r in assessment["risks"] if r["metric_code"] == metric_code)
    return risk.get("risk_level")


def _narrow_door_two_bed_room(scheme_inputs):
    """0.5m 窄门 + 两张床：最窄通道被门宽卡住，家具调整无法达标。"""
    m, s = scheme_inputs
    m["openings"][0]["width_m"] = 0.5
    s["doors"][0]["size"][0] = 0.5
    center = [3.0, 1.0, 0.3]
    m["objects"].append({"id": "bed_002", "instance_id": "bed_002", "type": "bed",
                         "center": list(center), "length_m": 2.0, "width_m": 1.2,
                         "height_m": 0.6, "rotation_z_deg": 0.0,
                         "measurement_status": "verified", "confidence": "medium"})
    s["semantic_instances"].append({"instance_id": "bed_002", "label": "bed",
                                    "center": list(center), "size": [2.0, 1.2, 0.6]})
    return m, s


def test_candidate_edits_never_touch_bed_without_need(scheme_inputs):
    m, s = scheme_inputs
    candidates = candidate_edits(m, s, "furniture_spacing", max_steps=2)
    assert candidates and all(isinstance(c, list) and c for c in candidates)
    assert all(c[0]["target"] in {"desk_001", "bed_001"} for c in candidates)


def test_candidate_edits_for_construction_metric_only_resize_door(scheme_inputs):
    m, s = scheme_inputs
    candidates = candidate_edits(m, s, "door_width")
    assert candidates == [[{"op": "resize_door", "width_m": 0.90}]]


def test_solver_never_claims_unverified_improvement(scheme_inputs):
    m, s = scheme_inputs
    for code in ("minimum_passage_width", "crowding", "door_width"):
        result = solve_metric(m, s, code)
        assert result["metric_code"] == code
        if result["solved"]:
            replay = build_scheme(m, s, result["edits"])
            assert replay["overall"]["score"] == result["score"]["after"]
        else:
            assert result["shortfall"]


def test_solver_marks_door_widening_as_construction(scheme_inputs):
    m, s = scheme_inputs
    result = solve_metric(m, s, "door_width")
    assert result["requires_construction"] is True
    assert any(e["op"] == "resize_door" for e in result["edits"])


def test_solver_respects_budget(scheme_inputs):
    m, s = scheme_inputs
    result = solve_metric(m, s, "minimum_passage_width", budget=1)
    assert result["evaluations"] <= 1
    assert result["budget_exhausted"] is True


def test_solver_result_shape(scheme_inputs):
    m, s = scheme_inputs
    result = solve_metric(m, s, "crowding", budget=5)
    for key in ("metric_code", "risk_name", "solved", "edits", "before", "after",
                "score", "shortfall", "requires_construction", "evaluations",
                "elapsed_ms", "budget_exhausted"):
        assert key in result


def test_solver_budget_counts_conflicting_candidates(scheme_inputs, monkeypatch):
    """预算约束官方链路调用次数：冲突候选同样跑过一次 build_scheme。"""
    m, s = scheme_inputs
    m["objects"][0]["center"] = [3.0, 2.0, 0.3]  # 床贴 +x 墙：首个候选必然越界冲突
    real = scheme_solver.build_scheme
    calls = []

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(scheme_solver, "build_scheme", spy)
    result = solve_metric(m, s, "crowding", budget=1)

    assert result["evaluations"] == 1
    assert result["budget_exhausted"] is True
    assert len(calls) == 2, "基线 1 次 + 预算内候选 1 次；冲突候选也必须计入预算"


def test_solver_prefers_small_move_over_removal(scheme_inputs):
    m, s = scheme_inputs
    result = solve_metric(m, s, "bedside_clearance")
    assert result["solved"] is True
    assert result["edits"], "达标方案应给出具体编辑"
    assert all(e["op"] == "move" for e in result["edits"]), "平移即可达标时不应搬出家具"


def test_solver_partial_result_is_best_improvement(scheme_inputs):
    m, s = _narrow_door_two_bed_room(scheme_inputs)
    result = solve_metric(m, s, "minimum_passage_width")

    assert result["solved"] is False
    assert result["shortfall"] and "None" not in result["shortfall"]

    outcomes = []
    for edits in candidate_edits(m, s, "minimum_passage_width"):
        replay = build_scheme(m, s, edits)
        if replay["conflicts"]:
            continue
        level = _level(replay, "minimum_passage_width")
        assert level not in (None, "low"), "该房间的候选不应达标，否则测试前提不成立"
        outcomes.append((replay["overall"]["score"], level, edits))

    assert len(outcomes) > 1, "应存在多个未达标候选"
    best = max(outcomes, key=lambda item: (item[0], -_LEVEL_RANK[item[1]]))
    assert len([item for item in outcomes if item[0] == best[0]]) == 1, "该 fixture 应有唯一最优改进"
    worst_score = min(item[0] for item in outcomes)
    assert result["score"]["after"] == best[0]
    assert result["score"]["after"] > worst_score
    assert result["edits"] == best[2]
    assert result["after"]["level"] == best[1]

def test_solver_never_reports_solved_for_not_evaluable_metric():
    """目标指标不可评估时必须诚实返回 solved:false 且不虚构分数提升。"""
    m = {
        "room": {"length_m": 4.0, "width_m": 4.0, "height_m": 2.7,
                 "measurement_status": "verified", "confidence": "medium"},
        "openings": [],
        "objects": [],
    }
    s = {
        "room": {"bounds_xy": {"min": [0.0, 0.0], "max": [4.0, 4.0]}, "height_m": 2.7},
        "walls": [], "geometric_obstacles": [], "doors": [],
        "semantic_instances": [],
    }
    result = solve_metric(m, s, "door_width")
    assert result["solved"] is False
    assert result["shortfall"] == "该指标当前不可评估，无法给出达标方案。"
    assert result["edits"] == []
    assert result["score"]["after"] == result["score"]["before"], "不可评估时不得声称分数改善"

