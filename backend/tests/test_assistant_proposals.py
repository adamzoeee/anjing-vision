"""AI suggestions must earn their geometry, numbers and access permissions."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.models import Scan
from assistant.deepseek_client import DeepSeekInvalidResponse, DeepSeekUnavailable
from pipeline.scheme_assessment import build_scheme, diff_assessments


def _engine():
    from assistant.proposal_engine import generate_room_proposals
    return generate_room_proposals


def _candidate(edits, title="Free bedside space"):
    return {"title": title, "intent": "Improve room access", "edits": edits,
            "risk_codes": ["bedside_clearance_high"]}


def _valid_candidates():
    return [
        _candidate([{"op": "move", "target": "desk_001", "dx": 0, "dy": -0.6}]),
        _candidate([{"op": "resize_door", "width_m": 1.0}], "Widen entrance"),
        _candidate([{"op": "move", "target": "bed_001", "dx": 1.8, "dy": -1}], "Move bed"),
    ]


def test_each_candidate_uses_its_own_formal_assessment(scheme_inputs):
    m, s = scheme_inputs
    original = deepcopy((m, s))
    proposals = _valid_candidates()
    # Model-authored numbers must never be trusted.
    proposals[0]["comparison"] = {"after_score": 999}
    result = _engine()(m, s, None, lambda context, feedback: proposals)
    assert result["status"] == "ready"
    assert result["rounds"] == 1
    assert len(result["proposals"]) == 3
    baseline = build_scheme(m, s, [])
    assert baseline["overall"]["score"] == 84.0
    for proposal in result["proposals"]:
        calculated = build_scheme(m, s, proposal["edits"])
        assert proposal["metrics"] == calculated["metrics"]
        assert proposal["risks"] == calculated["risks"]
        assert proposal["overall"] == calculated["overall"]
        assert proposal["layout_before"] == calculated["layout_before"]
        assert proposal["layout_after"] == calculated["layout_after"]
        for key, expected in diff_assessments(baseline, calculated).items():
            assert proposal[key] == expected
        assert proposal["evidence"]["verification"]["improvements"]
        assert proposal["evidence"]["facts"]["source"] == "structured_scan"
        assert proposal["limitations"] and proposal["disclaimer"]
    assert [p["comparison"]["after_score"] for p in result["proposals"]] == [92.0, 86.7, 86.7]
    assert (m, s) == original


def test_context_has_real_ids_and_explicit_schema_without_private_inputs(scheme_inputs):
    m, s = scheme_inputs
    m["media_path"] = "private/video.mp4"
    m["account"] = {"email": "private@example.com"}
    s["pointcloud"] = "private.ply"
    seen = []
    def request(context, feedback):
        seen.append(context)
        return _valid_candidates()
    _engine()(m, s, "Leave care space", request)
    context = seen[0]
    assert {obj["id"] for obj in context["objects"]} == {"bed_001", "desk_001"}
    assert context["openings"][0]["id"] == "door_01"
    assert context["goal"] == "Leave care space"
    assert context["allowed_edits"]["move"]["properties"]["target"]["enum"] == ["bed_001", "desk_001"]
    assert context["allowed_edits"]["move"]["properties"]["dx"]["maximum"] == 4.0
    assert "private" not in json.dumps(context)
    assert context["risks"] and context["score"] == 84.0


def test_context_offers_distinct_server_verified_starters(scheme_inputs):
    from assistant.proposal_engine import validate_room_candidate

    m, s = scheme_inputs
    seen = []
    _engine()(m, s, None, lambda context, feedback: seen.append(context) or [])
    starters = seen[0]["verified_starters"]
    assert 2 <= len(starters) <= 4
    strategies = set()
    for starter in starters:
        assert set(starter) == {"edits", "risk_codes"}
        candidate = {"title": "Model wording", "intent": "Model design intent", **starter}
        result, improvements = validate_room_candidate(m, s, candidate)
        assert improvements and not result["conflicts"]
        strategies.add(tuple((edit["op"], edit.get("target", "all_doors")) for edit in starter["edits"]))
    assert len(strategies) == len(starters)


def test_model_can_select_verified_starters_but_invalid_reply_is_not_filled(scheme_inputs):
    m, s = scheme_inputs

    def select(context, feedback):
        return [{"title": f"Model strategy {index}", "intent": "Model design intent", **starter}
                for index, starter in enumerate(context["verified_starters"][:3])]

    selected = _engine()(m, s, None, select)
    assert selected["status"] == "ready"
    assert len(selected["proposals"]) >= 2
    assert all(item["intent"] == "Model design intent" for item in selected["proposals"])

    def invent(context, feedback):
        return [_candidate([{"op": "move", "target": "desk_001", "dx": 4, "dy": 0}])]

    invalid = _engine()(m, s, None, invent)
    assert invalid["status"] == "insufficient"
    assert invalid["proposals"] == []
    assert "geometry_conflict" in {item["reason"] for item in invalid["rejections"]}


def test_scan46_style_room_offers_multiple_exact_starters_despite_narrow_door_and_early_furniture(scheme_inputs):
    m, s = scheme_inputs
    m["openings"][0]["center"][0] = 0.45
    s["doors"][0]["center"][0] = 0.45
    for index, kind in enumerate(("bookshelf", "table", "bookshelf", "table"), start=1):
        target = f"{kind}_{index:03d}"
        center = [3.5, 0.45 + (index - 1) * 0.9, 0.4]
        m["objects"].insert(index - 1, {
            "id": target, "instance_id": target, "type": kind, "center": center,
            "length_m": 0.3, "width_m": 0.3, "height_m": 0.8,
            "rotation_z_deg": 0, "measurement_status": "verified"})
        s["semantic_instances"].insert(index - 1, {
            "instance_id": target, "label": kind, "center": center, "size": [0.3, 0.3, 0.8]})

    seen = []

    def select(context, feedback):
        seen.append(context["verified_starters"])
        return [{"title": f"Model strategy {index}", "intent": "Improve access", **starter}
                for index, starter in enumerate(context["verified_starters"][:3])]

    result = _engine()(m, s, None, select)
    assert len(seen[0]) >= 2
    assert {item["edits"][0]["op"] for item in seen[0]} >= {"resize_door", "move"}
    assert {item["edits"][0].get("width_m") for item in seen[0]} >= {0.9, None}
    assert result["status"] == "ready"
    assert len(result["proposals"]) >= 2


def test_single_verified_starter_does_not_restrict_model_to_one_choice(scheme_inputs, monkeypatch):
    import assistant.proposal_engine as engine

    monkeypatch.setattr(engine, "_verified_starters", lambda *args: [
        {"edits": [{"op": "resize_door", "width_m": 1.0}], "risk_codes": ["door_width_medium"]}])
    m, s = scheme_inputs

    def request(context, feedback):
        assert context["verified_starters"] == []
        return _valid_candidates()[:2]

    result = _engine()(m, s, None, request)
    assert result["status"] == "ready"


def test_starter_search_stops_when_its_time_budget_expires(scheme_inputs, monkeypatch):
    import assistant.proposal_engine as engine

    clock = iter((0.0, 60.0))
    monkeypatch.setattr(engine, "monotonic", lambda: next(clock), raising=False)
    m, s = scheme_inputs
    baseline = build_scheme(m, s, [])
    assert engine._verified_starters(m, s, baseline) == []


@pytest.mark.parametrize("edit,reason", [
    ({"op": "move", "target": "missing", "dx": 0, "dy": -0.6}, "unknown_target"),
    ({"op": "move", "target": "desk_001", "dx": float("nan"), "dy": 0}, "invalid_number"),
    ({"op": "move", "target": "desk_001", "dx": float("inf"), "dy": 0}, "invalid_number"),
    ({"op": "move", "target": "desk_001", "dx": 1e30, "dy": 0}, "invalid_number"),
    ({"op": "move", "target": "desk_001", "dx": True, "dy": 0}, "invalid_number"),
    ({"op": "move", "target": "desk_001", "dx": "1", "dy": 0}, "invalid_number"),
    ({"op": "move", "target": "desk_001", "dx": 0, "dy": 0, "url": "https://example.com"}, "invalid_edit"),
    ({"op": "execute", "target": "desk_001"}, "unsupported_operation"),
    ({"op": "resize_door", "target": "missing", "width_m": 1.0}, "invalid_edit"),
    ({"op": "resize_door", "width_m": 9}, "invalid_number"),
    ({"op": "rotate", "target": "desk_001", "quarter_turns": 1.5}, "invalid_number"),
    ({"op": "add", "label": "chair", "center": [3, 1, 0.4], "size": [-1, 0.4, 0.8]}, "invalid_number"),
    ({"op": "move", "target": "desk_001", "dx": 0.2, "dy": 0.9}, "geometry_conflict"),
    ({"op": "move", "target": "desk_001", "dx": 4, "dy": 0}, "geometry_conflict"),
    ({"op": "move", "target": "desk_001", "dx": 0, "dy": 0}, "no_effect"),
    ({"op": "rotate", "target": "desk_001", "quarter_turns": 1}, "geometry_conflict"),
    ({"op": "remove", "target": "desk_001"}, "lost_evaluability"),
    ({"op": "resize_door", "width_m": 0.5}, "risk_worsened"),
])
def test_invalid_candidates_never_become_recommendations(scheme_inputs, edit, reason):
    m, s = scheme_inputs
    result = _engine()(m, s, None, lambda context, feedback: [_candidate([edit])])
    assert result["status"] == "insufficient"
    assert result["rounds"] == 2
    assert result["proposals"] == []
    assert reason in {r["reason"] for r in result["rejections"]}
    assert result["message"]


def test_near_identical_moves_do_not_fill_two_slots(scheme_inputs):
    m, s = scheme_inputs
    candidates = [_valid_candidates()[0], _candidate([
        {"op": "move", "target": "desk_001", "dx": 0, "dy": -0.65}], "Another title")]
    result = _engine()(m, s, None, lambda context, feedback: candidates)
    assert result["status"] == "insufficient"
    assert result["proposals"] == []
    assert "duplicate_strategy" in {r["reason"] for r in result["rejections"]}


def test_one_revision_keeps_verified_candidate_and_adds_distinct_strategy(scheme_inputs):
    m, s = scheme_inputs
    requests = []
    def request(context, feedback):
        requests.append(deepcopy(feedback))
        if feedback is None:
            return [_valid_candidates()[0], _candidate([{"op": "remove", "target": "missing"}])]
        assert any(item["reason"] == "unknown_target" for item in feedback)
        return [_valid_candidates()[1]]
    result = _engine()(m, s, None, request)
    assert result["status"] == "ready"
    assert result["rounds"] == len(requests) == 2
    assert len(result["proposals"]) == 2
    assert result["proposals"][0]["comparison"]["after_score"] == 92.0
    assert result["proposals"][1]["comparison"]["after_score"] == 86.7


def test_noop_actions_cannot_make_centimetre_variants_distinct(scheme_inputs):
    m, s = scheme_inputs
    variant = _candidate([
        {"op": "move", "target": "desk_001", "dx": 0, "dy": -0.65},
        {"op": "resize_door", "width_m": 0.85},
    ])
    result = _engine()(m, s, None, lambda context, feedback: [_valid_candidates()[0], variant])
    assert result["status"] == "insufficient"
    assert result["proposals"] == []


def test_relabeling_assumed_object_does_not_make_a_new_strategy(scheme_inputs):
    m, s = scheme_inputs
    candidates = []
    for label in ("chair", "cabinet"):
        candidates.append(_candidate([
            {"op": "move", "target": "desk_001", "dx": 0, "dy": -0.6},
            {"op": "add", "label": label, "center": [3.5, 3.5, 0.25], "size": [0.3, 0.3, 0.5]},
        ]))
    result = _engine()(m, s, None, lambda context, feedback: candidates)
    assert result["status"] == "insufficient"
    assert result["proposals"] == []


@pytest.mark.parametrize("obstacle_collection", ["geometric_obstacles", "walls", "semantic_instances"])
def test_moves_cannot_overlap_structured_obstacles(scheme_inputs, obstacle_collection):
    m, s = scheme_inputs
    s[obstacle_collection].append({"instance_id": "fixed_obstacle", "id": "fixed_obstacle",
                                   "label": "cabinet", "center": [1.2, 1.4, 0.2],
                                   "size": [0.4, 0.4, 0.4], "rotation_z_deg": 0})
    result = _engine()(m, s, None, lambda context, feedback: _valid_candidates()[:2])
    assert result["status"] == "insufficient"
    assert result["proposals"] == []
    assert "geometry_conflict" in {r["reason"] for r in result["rejections"]}


def test_assumed_furniture_cannot_overlap_structured_obstacle(scheme_inputs):
    m, s = scheme_inputs
    s["geometric_obstacles"].append({"instance_id": "fixed_box", "center": [3.5, 3.5, 0.25],
                                      "size": [0.4, 0.4, 0.5]})
    invalid = _candidate([
        {"op": "move", "target": "desk_001", "dx": 0, "dy": -0.6},
        {"op": "add", "label": "chair", "center": [3.5, 3.5, 0.25], "size": [0.3, 0.3, 0.5]},
    ])
    result = _engine()(m, s, None, lambda context, feedback: [invalid, _valid_candidates()[1]])
    assert result["status"] == "insufficient"
    assert "geometry_conflict" in {r["reason"] for r in result["rejections"]}


@pytest.mark.parametrize("height", [1.0, 1.4])
def test_vertical_separation_from_structured_obstacle_is_allowed(scheme_inputs, height):
    m, s = scheme_inputs
    # Desk top is z=0.8: the obstacle either touches its top or is above it.
    s["geometric_obstacles"].append({"instance_id": "raised_box", "center": [1.2, 1.4, height],
                                      "size": [0.4, 0.4, 0.4]})
    result = _engine()(m, s, None, lambda context, feedback: _valid_candidates()[:2])
    assert result["status"] == "ready"
    assert len(result["proposals"]) == 2
    assert result["proposals"][0]["edits"] == _valid_candidates()[0]["edits"]


def test_cancelling_rotations_do_not_make_a_second_move_strategy(scheme_inputs):
    m, s = scheme_inputs
    variant = _candidate([
        {"op": "move", "target": "desk_001", "dx": 0, "dy": -0.65},
        {"op": "rotate", "target": "desk_001", "quarter_turns": 1},
        {"op": "rotate", "target": "desk_001", "quarter_turns": 3},
    ])
    result = _engine()(m, s, None, lambda context, feedback: [_valid_candidates()[0], variant])
    assert result["status"] == "insufficient"
    assert result["proposals"] == []
    assert "duplicate_strategy" in {r["reason"] for r in result["rejections"]}


def test_rotation_proposal_uses_truly_rotated_footprint(scheme_inputs):
    m, s = scheme_inputs
    candidate = _candidate([
        {"op": "move", "target": "desk_001", "dx": 0, "dy": -0.6},
        {"op": "rotate", "target": "desk_001", "quarter_turns": 1},
    ])
    result = _engine()(m, s, None, lambda context, feedback: [candidate, _valid_candidates()[1]])
    assert result["status"] == "ready"
    rotated = result["proposals"][0]
    assert rotated["layout_after"]["objects"][1]["size"] == [1.0, 0.5, 0.8]
    assert rotated["layout_after"]["objects"][1]["rotation_z_deg"] == 90
    clearance = next(change for change in rotated["metric_changes"] if change["metric_code"] == "bedside_clearance")
    assert clearance["after"] == pytest.approx(0.5)


def test_door_proposal_cannot_widen_beyond_room(scheme_inputs):
    m, s = scheme_inputs
    candidate = _candidate([{"op": "resize_door", "width_m": 1.5}])
    result = _engine()(m, s, None, lambda context, feedback: [candidate, _valid_candidates()[0]])
    assert result["status"] == "insufficient"
    assert "geometry_conflict" in {r["reason"] for r in result["rejections"]}


def test_single_candidate_validator_returns_verified_result_and_improvements(scheme_inputs):
    from assistant.proposal_engine import validate_room_candidate
    m, s = scheme_inputs
    result, improvements = validate_room_candidate(m, s, _valid_candidates()[0])
    assert result["overall"]["score"] == 92.0
    assert result["layout_after"]["objects"][1]["center"] == [1.2, 1.4, 0.4]
    assert improvements == [{"metric_code": "bedside_clearance", "before": 0.15, "after": 0.75,
                              "unit": "m", "risk_before": "high", "risk_after": "low"}]


@pytest.mark.parametrize("edit,reason", [
    ({"op": "move", "target": "missing", "dx": 0, "dy": -0.6}, "unknown_target"),
    ({"op": "move", "target": "desk_001", "dx": float("nan"), "dy": 0}, "invalid_number"),
    ({"op": "move", "target": "desk_001", "dx": 0, "dy": 0}, "no_effect"),
    ({"op": "rotate", "target": "desk_001", "quarter_turns": 1}, "geometry_conflict"),
    ({"op": "rotate", "target": "desk_001", "quarter_turns": 2}, "no_improvement"),
    ({"op": "remove", "target": "desk_001"}, "lost_evaluability"),
    ({"op": "resize_door", "width_m": 0.5}, "risk_worsened"),
    ({"op": "resize_door", "width_m": 1.5}, "geometry_conflict"),
])
def test_single_candidate_validator_enforces_generation_checks(scheme_inputs, edit, reason):
    from assistant.proposal_engine import ProposalRejected, validate_room_candidate
    m, s = scheme_inputs
    with pytest.raises(ProposalRejected) as error:
        validate_room_candidate(m, s, _candidate([edit]))
    assert error.value.reason == reason
    assert str(error.value) == reason


def test_single_candidate_validator_rejects_structured_collision(scheme_inputs):
    from assistant.proposal_engine import ProposalRejected, validate_room_candidate
    m, s = scheme_inputs
    s["geometric_obstacles"].append({"center": [1.2, 1.4, 0.2], "size": [0.4, 0.4, 0.4]})
    with pytest.raises(ProposalRejected, match="geometry_conflict"):
        validate_room_candidate(m, s, _valid_candidates()[0])


def test_single_candidate_validator_rejects_invalid_description_and_risk(scheme_inputs):
    from assistant.proposal_engine import ProposalRejected, validate_room_candidate
    m, s = scheme_inputs
    candidate = _valid_candidates()[0]
    candidate["intent"] = "x" * 601
    with pytest.raises(ProposalRejected, match="invalid_description"):
        validate_room_candidate(m, s, candidate)
    candidate["intent"] = "Provide care space"
    candidate["risk_codes"] = ["unverified_risk"]
    with pytest.raises(ProposalRejected, match="unknown_risk"):
        validate_room_candidate(m, s, candidate)


def test_api_changed_inputs_during_generation_return_conflict(client, scheme_inputs, monkeypatch):
    route = _fake_model(monkeypatch)
    sid, headers, post = _scan(client, scheme_inputs)
    def request(context, feedback, **kwargs):
        path = post / "measurements.json"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return _valid_candidates()
    monkeypatch.setattr(route, "request_candidates", request)
    response = client.post(f"/api/assistant/scans/{sid}/proposals", json={}, headers=headers)
    assert response.status_code == 409


def test_api_invalid_structured_data_is_a_controlled_conflict(client, scheme_inputs, monkeypatch):
    _fake_model(monkeypatch)
    sid, headers, post = _scan(client, scheme_inputs)
    (post / "measurements.json").write_text("not JSON", encoding="utf-8")
    response = client.post(f"/api/assistant/scans/{sid}/proposals", json={}, headers=headers)
    assert response.status_code == 409


def _scan(client, scheme_inputs, *, status="done", capture_type="video", files=True):
    from conftest import TestingSessionLocal
    client.post("/api/auth/register", json={"org_name": "A", "name": "A", "email": "proposal@x.com", "password": "secret123"})
    token = client.post("/api/auth/login", json={"email": "proposal@x.com", "password": "secret123"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    pid = client.post("/api/projects", json={"name": "Room"}, headers=headers).json()["id"]
    with TestingSessionLocal() as db:
        scan = Scan(project_id=pid, status=status, capture_type=capture_type)
        db.add(scan)
        db.commit()
        sid = scan.id
    post = Path(get_settings().data_dir) / "work" / str(sid) / "postprocess"
    if files:
        post.mkdir(parents=True, exist_ok=True)
        for name, value in zip(("measurements.json", "structure_calibrated.json"), scheme_inputs):
            (post / name).write_text(json.dumps(value), encoding="utf-8")
    return sid, headers, post


def _fake_model(monkeypatch, response=None):
    import app.routers.assistant_proposals as route
    monkeypatch.setattr(get_settings(), "deepseek_api_key", "fake-test-key")
    def request(context, feedback, **kwargs):
        assert kwargs["api_key"] == "fake-test-key"
        assert kwargs["model"] == "deepseek-flash"
        return _valid_candidates() if response is None else response
    monkeypatch.setattr(route, "request_candidates", request)
    return route


@pytest.mark.parametrize("capture_type", ["video", "photos"])
def test_api_accepts_completed_single_room_and_returns_fingerprints(client, scheme_inputs, monkeypatch, capture_type):
    from app.routers.renovation import _input_fingerprints
    _fake_model(monkeypatch)
    sid, headers, post = _scan(client, scheme_inputs, capture_type=capture_type)
    files_before = {p.name: p.read_bytes() for p in post.iterdir()}
    response = client.post(f"/api/assistant/scans/{sid}/proposals", json={"goal": "Care access"}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready" and len(body["proposals"]) == 3
    assert body["scan_id"] == sid
    assert body["input_fingerprints"] == _input_fingerprints(post.parent)
    assert {p.name: p.read_bytes() for p in post.iterdir()} == files_before
    assert "fake-test-key" not in response.text


def test_api_uses_configured_provider_endpoint_and_token_cap(client, scheme_inputs, monkeypatch):
    import app.routers.assistant_proposals as route
    from types import SimpleNamespace
    from app.main import app
    settings = get_settings()
    configured = SimpleNamespace(**{**settings.model_dump(), "deepseek_api_key": "fake-test-key",
                                    "deepseek_endpoint": "https://example.test/v1/chat/completions",
                                    "deepseek_max_tokens": 1024})
    app.dependency_overrides[get_settings] = lambda: configured
    def request(context, feedback, **kwargs):
        assert kwargs["endpoint"] == "https://example.test/v1/chat/completions"
        assert kwargs["max_tokens"] == 1024
        assert kwargs["api_key"] == "fake-test-key"
        return _valid_candidates()
    monkeypatch.setattr(route, "request_candidates", request)
    sid, headers, _ = _scan(client, scheme_inputs)
    response = client.post(f"/api/assistant/scans/{sid}/proposals", json={}, headers=headers)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("status,capture_type,files", [
    ("processing", "video", True), ("uploading", "photos", True),
    ("done", "house", True), ("done", "house_demo", True), ("done", "video", False),
])
def test_api_rejects_unready_missing_and_whole_house_scans(client, scheme_inputs, monkeypatch, status, capture_type, files):
    _fake_model(monkeypatch)
    sid, headers, _ = _scan(client, scheme_inputs, status=status, capture_type=capture_type, files=files)
    response = client.post(f"/api/assistant/scans/{sid}/proposals", json={}, headers=headers)
    assert response.status_code == 409, response.text


def test_api_is_scoped_to_organization(client, scheme_inputs, monkeypatch):
    _fake_model(monkeypatch)
    sid, _, _ = _scan(client, scheme_inputs)
    client.post("/api/auth/register", json={"org_name": "B", "name": "B", "email": "other@x.com", "password": "secret123"})
    token = client.post("/api/auth/login", json={"email": "other@x.com", "password": "secret123"}).json()["token"]
    response = client.post(f"/api/assistant/scans/{sid}/proposals", json={}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


@pytest.mark.parametrize("exception,status", [(DeepSeekUnavailable, 503), (DeepSeekInvalidResponse, 502)])
def test_api_provider_errors_are_sanitized(client, scheme_inputs, monkeypatch, exception, status):
    route = _fake_model(monkeypatch)
    def fail(*args, **kwargs):
        raise exception("fake-test-key private upstream diagnostic")
    monkeypatch.setattr(route, "request_candidates", fail)
    sid, headers, _ = _scan(client, scheme_inputs)
    response = client.post(f"/api/assistant/scans/{sid}/proposals", json={}, headers=headers)
    assert response.status_code == status
    assert "fake-test-key" not in response.text
    assert "private" not in response.text


def test_api_missing_key_and_insufficient_results_are_explicit(client, scheme_inputs, monkeypatch):
    _fake_model(monkeypatch, [])
    sid, headers, _ = _scan(client, scheme_inputs)
    response = client.post(f"/api/assistant/scans/{sid}/proposals", json={}, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["message"]
    monkeypatch.setattr(get_settings(), "deepseek_api_key", None)
    response = client.post(f"/api/assistant/scans/{sid}/proposals", json={}, headers=headers)
    assert response.status_code == 503


def test_goal_limit_is_validated(client, scheme_inputs, monkeypatch):
    _fake_model(monkeypatch)
    sid, headers, _ = _scan(client, scheme_inputs)
    response = client.post(f"/api/assistant/scans/{sid}/proposals", json={"goal": "x" * 301}, headers=headers)
    assert response.status_code == 422
