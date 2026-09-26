import pytest

from pipeline.scheme_assessment import build_scheme
from pipeline.scheme_solver import candidate_edits, solve_metric


def test_candidate_edits_never_touch_bed_without_need(scheme_inputs):
    m, s = scheme_inputs
    candidates = candidate_edits(m, s, "furniture_spacing", max_steps=2)
    assert candidates and all(isinstance(c, list) and c for c in candidates)
    assert all(c[0]["target"] in {"desk_001", "bed_001"} for c in candidates)


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
