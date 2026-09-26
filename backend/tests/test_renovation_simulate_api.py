"""改造模拟 API：同源重算、布局返回与约束冲突。"""
import json

from app.config import get_settings
from pipeline.scheme_assessment import assess_scheme
from assistant.intent_edits import intent_to_edits, resolve_target
from pathlib import Path


def _auth(client, org="养老院A", email="sandbox@x.com"):
    client.post("/api/auth/register", json={
        "org_name": org, "name": "甲", "email": email, "password": "secret123"})
    r = client.post("/api/auth/login", json={"email": email, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _scan_with_artifacts(client, headers, measurements, structure):
    pid = client.post("/api/projects", json={"name": "沙盒房间"}, headers=headers).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"},
                      headers=headers).json()["id"]
    post = Path(get_settings().data_dir) / "work" / str(sid) / "postprocess"
    post.mkdir(parents=True, exist_ok=True)
    (post / "measurements.json").write_text(json.dumps(measurements, ensure_ascii=False), encoding="utf-8")
    (post / "structure_calibrated.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
    return sid


def test_simulate_with_empty_edits_matches_official_assessment(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, m, s)
    expected = assess_scheme(m, s)["overall"]["score"]
    r = client.post(f"/api/assistant/scans/{sid}/simulate", json={"edits": []}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["overall"]["score"] == expected
    assert body["scope"] == {"simulation": True, "raw_media_accessed": False,
                             "structured_inputs_only": True, "contains_assumed_objects": False}


def test_simulate_returns_layouts_and_changes(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client, email="layout@x.com")
    sid = _scan_with_artifacts(client, h, m, s)
    r = client.post(f"/api/assistant/scans/{sid}/simulate",
                    json={"edits": [{"op": "move", "target": "desk_001", "dx": 0.0, "dy": -1.0}]},
                    headers=h)
    body = r.json()
    assert r.status_code == 200
    assert body["layout_before"]["counts"]["objects"] == 2
    assert body["layout_after"]["counts"]["objects"] == 2
    desk_after = next(o for o in body["layout_after"]["objects"] if o["id"] == "desk_001")
    desk_before = next(o for o in body["layout_before"]["objects"] if o["id"] == "desk_001")
    assert desk_after["center"][1] == desk_before["center"][1] - 1.0
    assert body["changed_ids"] == ["desk_001"]
    assert body["metric_changes"], "位移必须引起指标变化"
    assert body["comparison"]["after_score"] is not None


def test_simulate_reports_conflicts_without_changing_score(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client, email="conflict@x.com")
    sid = _scan_with_artifacts(client, h, m, s)
    baseline = client.post(f"/api/assistant/scans/{sid}/simulate", json={"edits": []}, headers=h).json()
    r = client.post(f"/api/assistant/scans/{sid}/simulate",
                    json={"edits": [{"op": "move", "target": "desk_001", "dx": 0.2, "dy": 0.9}]}, headers=h)
    body = r.json()
    assert body["conflicts"] and body["conflicts"][0]["type"] == "overlap"
    assert body["overall"]["score"] == baseline["overall"]["score"]
    assert body["applied_edits"] == []


def test_simulate_rejects_missing_artifacts(client):
    h = _auth(client, email="missing@x.com")
    pid = client.post("/api/projects", json={"name": "空房间"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    r = client.post(f"/api/assistant/scans/{sid}/simulate", json={"edits": []}, headers=h)
    assert r.status_code == 409


def test_intent_to_edits_resolves_chinese_alias(scheme_inputs):
    m, s = scheme_inputs
    assert resolve_target(m, s, "书桌") == "desk_001"
    edits = intent_to_edits({"action": "MOVE", "target": "书桌", "dx": -1.0, "dy": 0.0, "distance_m": 0.3}, m, s)
    assert edits == [{"op": "move", "target": "desk_001", "dx": -0.3, "dy": 0.0}]