"""建议列表：达成预判标签。"""
import json
from pathlib import Path

from app.config import get_settings


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
