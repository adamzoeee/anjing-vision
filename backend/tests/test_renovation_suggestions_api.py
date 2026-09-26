"""建议列表与求解接口：达成预判标签 + 沙盒 scope。"""
import json
from pathlib import Path

from app.config import get_settings

_MANDATED_SCOPE_KEYS = {"simulation": True, "raw_media_accessed": False, "structured_inputs_only": True}


def _auth(client, email="reach@x.com"):
    client.post("/api/auth/register", json={
        "org_name": "养老院A", "name": "甲", "email": email, "password": "secret123"})
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


def test_suggestions_expose_reachability_labels(client, scheme_inputs):
    m, s = scheme_inputs
    headers = _auth(client)
    sid = _scan_with_artifacts(client, headers, m, s)
    body = client.get(f"/api/assistant/scans/{sid}/suggestions", headers=headers).json()
    assert body["suggestions"], "合成房间应至少给出一项风险建议"
    for item in body["suggestions"]:
        assert item["reachability"] in {"movable", "construction", "unknown"}
        assert item["reachability_label"]
        assert item["metric_code"]
    assert {key: body["scope"][key] for key in _MANDATED_SCOPE_KEYS} == _MANDATED_SCOPE_KEYS
    assert "contains_assumed_objects" in body["scope"]


def test_solve_returns_scope_and_results(client, scheme_inputs):
    m, s = scheme_inputs
    headers = _auth(client, email="solve@x.com")
    sid = _scan_with_artifacts(client, headers, m, s)
    body = client.post(f"/api/assistant/scans/{sid}/solve",
                       json={"metric_codes": ["door_width"]}, headers=headers).json()
    assert body["scan_id"] == sid
    assert {key: body["scope"][key] for key in _MANDATED_SCOPE_KEYS} == _MANDATED_SCOPE_KEYS
    assert "contains_assumed_objects" in body["scope"]
    assert [item["metric_code"] for item in body["results"]] == ["door_width"]
    assert any(e["op"] == "resize_door" for e in body["results"][0]["edits"])
