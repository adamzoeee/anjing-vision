"""方案 CRUD：机构隔离、校验与可复现性。"""
import io
import json
from pathlib import Path

import PIL.Image

from app.config import get_settings
from app.routers.renovation import MAX_PLAN_BYTES


def _auth(client, org="养老院A", email="scheme@x.com"):
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


def test_scheme_crud_and_reproducible_score(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, m, s)
    edits = [{"op": "move", "target": "desk_001", "dx": 0.0, "dy": -0.6}]
    created = client.post(f"/api/assistant/scans/{sid}/schemes",
                          json={"name": "方案A", "edits": edits}, headers=h)
    assert created.status_code == 200
    scheme_id = created.json()["id"]

    listed = client.get(f"/api/assistant/scans/{sid}/schemes", headers=h).json()
    assert len(listed["schemes"]) == 1
    listed_score = listed["schemes"][0]["score"]

    detail = client.get(f"/api/assistant/scans/{sid}/schemes/{scheme_id}", headers=h)
    assert detail.status_code == 200
    body = detail.json()
    assert body["score"] == listed_score
    assert body["layout_after"]["counts"]["objects"] == 2
    assert body["plan"]["edits"] == edits

    renamed = client.patch(f"/api/assistant/scans/{sid}/schemes/{scheme_id}",
                           json={"name": "方案A · 书桌下移"}, headers=h)
    assert renamed.json()["name"] == "方案A · 书桌下移"

    assert client.delete(f"/api/assistant/scans/{sid}/schemes/{scheme_id}", headers=h).status_code == 200
    assert client.get(f"/api/assistant/scans/{sid}/schemes", headers=h).json()["schemes"] == []


def test_scheme_rejects_conflicting_edits(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client, email="bad-edits@x.com")
    sid = _scan_with_artifacts(client, h, m, s)
    r = client.post(f"/api/assistant/scans/{sid}/schemes",
                    json={"name": "撞床方案", "edits": [{"op": "move", "target": "desk_001", "dx": 0.2, "dy": 0.9}]},
                    headers=h)
    assert r.status_code == 422


def test_scheme_is_isolated_by_org(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, m, s)
    scheme_id = client.post(f"/api/assistant/scans/{sid}/schemes",
                            json={"name": "方案A", "edits": []}, headers=h).json()["id"]
    h2 = _auth(client, org="养老院B", email="other@x.com")
    assert client.get(f"/api/assistant/scans/{sid}/schemes/{scheme_id}", headers=h2).status_code == 404
    assert client.get(f"/api/assistant/scans/{sid}/schemes", headers=h2).status_code == 404


def test_scheme_patch_delete_isolated_by_org(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, m, s)
    scheme_id = client.post(f"/api/assistant/scans/{sid}/schemes",
                            json={"name": "方案A", "edits": []}, headers=h).json()["id"]
    h2 = _auth(client, org="养老院B", email="other-edit@x.com")
    assert client.patch(f"/api/assistant/scans/{sid}/schemes/{scheme_id}",
                        json={"name": "越权改名"}, headers=h2).status_code == 404
    assert client.delete(f"/api/assistant/scans/{sid}/schemes/{scheme_id}",
                         headers=h2).status_code == 404


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    PIL.Image.new("RGB", (32, 32), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _export_scheme(client, headers, sid, scheme_id, payload):
    return client.post(
        f"/api/assistant/scans/{sid}/schemes/{scheme_id}/export.pdf",
        files={"plan": ("plan.png", payload, "image/png")}, headers=headers,
    )


def test_scheme_export_pdf_returns_pdf(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client, email="export@x.com")
    sid = _scan_with_artifacts(client, h, m, s)
    scheme_id = client.post(f"/api/assistant/scans/{sid}/schemes",
                            json={"name": "方案A", "edits": []}, headers=h).json()["id"]
    r = _export_scheme(client, h, sid, scheme_id, _png_bytes())
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")
    assert r.headers["content-type"] == "application/pdf"


def test_scheme_export_pdf_rejects_bad_or_oversized_plan(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client, email="export-bad@x.com")
    sid = _scan_with_artifacts(client, h, m, s)
    scheme_id = client.post(f"/api/assistant/scans/{sid}/schemes",
                            json={"name": "方案A", "edits": []}, headers=h).json()["id"]
    # 非 PNG 签名 → 422
    assert _export_scheme(client, h, sid, scheme_id, b"not a png").status_code == 422
    # 魔术字节正确但内容损坏 → 422 而不是 500
    corrupt = _export_scheme(client, h, sid, scheme_id, b"\x89PNG\r\n\x1a\n" + b"broken")
    assert corrupt.status_code == 422, corrupt.text
    # 超过 10MB 上限 → 413
    oversized = _export_scheme(client, h, sid, scheme_id, b"\x89PNG" + b"0" * MAX_PLAN_BYTES)
    assert oversized.status_code == 413, oversized.text
    # 跨机构导出仍 404
    h2 = _auth(client, org="养老院B", email="export-other@x.com")
    assert _export_scheme(client, h2, sid, scheme_id, _png_bytes()).status_code == 404
