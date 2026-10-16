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
    ({"op": "rotate", "target": "desk_001", "quarter_turns": 1}, "no_improvement"),
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
