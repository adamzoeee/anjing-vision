from pipeline.rules import evaluate_risks, compute_score


def test_evaluate_risks_door_width():
    risks = evaluate_risks({"door_width_m": 0.75})
    door = [r for r in risks if r["code"] == "door_width"][0]
    assert door["level"] == "red" and door["measure"] == 0.75
    risks2 = evaluate_risks({"door_width_m": 0.85})
    assert [r for r in risks2 if r["code"] == "door_width"][0]["level"] == "yellow"
    risks3 = evaluate_risks({"door_width_m": 0.95})
    assert [r for r in risks3 if r["code"] == "door_width"][0]["level"] == "green"


def test_evaluate_risks_obstacle_in_passage():
    risks = evaluate_risks({"obstacles_in_passage": [{"label": "纸箱", "count": 1}]})
    assert any(r["code"] == "obstacle" and r["level"] == "red" for r in risks)


def test_detected_furniture_alone_is_not_a_passage_risk():
    risks = evaluate_risks({"detected_objects": [{"label": "椅子", "count": 1}]})
    assert all(r["code"] != "obstacle" for r in risks)


def test_pending_and_confirmed_clear_obstacle_assessments_are_distinct():
    pending = evaluate_risks({"obstacles_in_passage": None})
    pending_obstacle = [risk for risk in pending if risk["code"] == "obstacle"][0]
    assert pending_obstacle["level"] == "unknown"
    assert pending_obstacle["measure"] is None

    confirmed_clear = evaluate_risks({"obstacles_in_passage": []})
    clear_obstacle = [risk for risk in confirmed_clear if risk["code"] == "obstacle"][0]
    assert clear_obstacle["level"] == "green"
    assert clear_obstacle["measure"] == []


def test_evaluate_risks_stairs_and_missing():
    risks = evaluate_risks({"stairs_exist": True})
    assert any(r["code"] == "stairs" and r["level"] == "red" for r in risks)
    # 未提供的测量项不产生风险
    assert all(r["code"] != "slope" for r in risks)


def test_compute_score_weights():
    score, detail = compute_score({"door_width_m": 0.75})
    assert 0 <= score <= 100
    assert "通行性" in detail["parts"]


def test_compute_score_all_green_is_100():
    score, _ = compute_score({"door_width_m": 1.2, "passage_width_m": 2.0,
                              "threshold_m": 0.005, "slope": 0.01,
                              "uneven_m": 0.005})
    assert score == 100.0


def test_compute_score_red_door_penalizes():
    score, detail = compute_score({"door_width_m": 0.7})
    assert score <= 60
    assert detail["parts"]["通行性"] <= 0.0 + 1e-6


def test_compute_score_uses_worst_risk_when_category_has_mixed_levels():
    score, detail = compute_score({
        "door_width_m": 0.7,
        "passage_width_m": 2.0,
        "bathroom_door_m": None,
    })

    assert detail["parts"]["通行性"] == 0.0
    assert score == 60.0


def test_compute_score_excludes_unknown_and_reports_assessment_completeness():
    measures = {
        "door_width_m": 1.0,
        "passage_width_m": 1.5,
        "threshold_m": 0.0,
        "stairs_exist": False,
        "slope": 0.01,
        "uneven_m": 0.005,
        "obstacles_in_passage": None,
        "obstacle_assessment_status": "pending_spatial_validation",
        "bathroom_door_m": None,
    }

    score, detail = compute_score(measures)

    assert score == 100.0
    assert detail["parts"] == {"通行性": 100.0, "跌倒风险": 100.0, "无障碍": 100.0}
    assert detail["assessment_completeness"] == {
        "known_count": 5,
        "unknown_count": 2,
        "percent": 71.4,
    }
    assert {risk["code"] for risk in detail["risks"] if risk["level"] == "unknown"} == {
        "bathroom_door",
        "obstacle",
    }


def test_compute_score_all_unknown_returns_none_instead_of_fake_perfect():
    """全部风险未知时不得给出假满分（历史问题：unknown 一律按 default=1.0 计 100 分）。"""
    measures = {
        "door_width_m": None,
        "passage_width_m": None,
        "threshold_m": None,
        "slope": None,
        "uneven_m": None,
        "obstacles_in_passage": None,
        "obstacle_assessment_status": "pending_spatial_validation",
        "bathroom_door_m": None,
    }

    score, detail = compute_score(measures)

    assert score is None
    assert detail["assessment_completeness"]["known_count"] == 0
    assert detail["assessment_completeness"]["percent"] == 0.0
