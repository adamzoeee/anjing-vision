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
