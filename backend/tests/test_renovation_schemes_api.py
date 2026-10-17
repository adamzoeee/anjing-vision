"""方案 CRUD：机构隔离、校验与可复现性。"""
import io
import json
from pathlib import Path
from uuid import uuid4

import PIL.Image
import pytest

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

def test_scheme_flags_stale_when_inputs_change(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client, email="stale@x.com")
    sid = _scan_with_artifacts(client, h, m, s)
    scheme_id = client.post(f"/api/assistant/scans/{sid}/schemes",
                            json={"name": "方案A", "edits": []}, headers=h).json()["id"]

    # 保存后输入指纹应一致 → 不标记过期
    fresh = client.get(f"/api/assistant/scans/{sid}/schemes/{scheme_id}", headers=h).json()
    assert fresh["stale"] is False
    assert fresh["based_on_mismatch"] is False
    assert fresh["mismatched_keys"] == []

    # 修改 measurements.json 文件内容 → 指纹变化 → 标记过期
    post = Path(get_settings().data_dir) / "work" / str(sid) / "postprocess"
    changed = dict(m)
    changed["room"] = {**m["room"], "width_m": 4.2}
    (post / "measurements.json").write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")

    listed = client.get(f"/api/assistant/scans/{sid}/schemes", headers=h).json()
    assert listed["schemes"][0]["stale"] is True
    assert listed["schemes"][0]["based_on_mismatch"] is True
    assert listed["schemes"][0]["mismatched_keys"]

    detail = client.get(f"/api/assistant/scans/{sid}/schemes/{scheme_id}", headers=h).json()
    assert detail["stale"] is True
    assert detail["based_on_mismatch"] is True
    assert detail["mismatched_keys"]

def test_scheme_export_cleans_up_temporary_pdf(client, scheme_inputs):
    m, s = scheme_inputs
    h = _auth(client, email="export-cleanup@x.com")
    sid = _scan_with_artifacts(client, h, m, s)
    scheme_id = client.post(f"/api/assistant/scans/{sid}/schemes",
                            json={"name": "方案A", "edits": []}, headers=h).json()["id"]
    r = _export_scheme(client, h, sid, scheme_id, _png_bytes())
    assert r.status_code == 200, r.text
    schemes_dir = Path(get_settings().data_dir) / "work" / str(sid) / "schemes"
    leftovers = list(schemes_dir.glob("*.pdf")) if schemes_dir.exists() else []
    assert leftovers == [], "导出后不得残留临时 PDF 文件"


def test_scheme_export_provenance_includes_stale_flags(client, scheme_inputs, monkeypatch):
    """导出 PDF 的 provenance 必须带上基于旧数据的标记，而不只是 stale。"""
    import app.routers.renovation as module

    m, s = scheme_inputs
    h = _auth(client, email="export-stale@x.com")
    sid = _scan_with_artifacts(client, h, m, s)
    scheme_id = client.post(f"/api/assistant/scans/{sid}/schemes",
                            json={"name": "方案A", "edits": []}, headers=h).json()["id"]

    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        Path(kwargs["output"]).write_bytes(b"%PDF-1.4\n%%EOF\n")
        return kwargs["output"]

    monkeypatch.setattr(module, "build_scheme_appendix_pdf", fake_build)

    png = io.BytesIO()
    PIL.Image.new("RGB", (20, 20), (255, 255, 255)).save(png, format="PNG")

    # 改动 postprocess 输入，使保存时的指纹与当前不一致
    post = Path(get_settings().data_dir) / "work" / str(sid) / "postprocess" / "measurements.json"
    payload = json.loads(post.read_text(encoding="utf-8"))
    payload["room"]["length_m"] = payload["room"]["length_m"] + 0.5
    post.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    r = client.post(f"/api/assistant/scans/{sid}/schemes/{scheme_id}/export.pdf",
                    files={"plan": ("plan.png", png.getvalue(), "image/png")}, headers=h)
    assert r.status_code == 200

    provenance = captured["provenance"]
    assert provenance["stale"] is True
    assert provenance["based_on_mismatch"] is True
    assert provenance["mismatched_keys"]


def _ai_save_payload(sid):
    import hashlib

    post = Path(get_settings().data_dir) / "work" / str(sid) / "postprocess"
    return {
        "name": "拓宽房门", "source": "ai",
        "edits": [{"op": "resize_door", "width_m": 1.0}],
        "based_on": {name.replace(".json", "_sha256"): hashlib.sha256((post / name).read_bytes()).hexdigest()
                     for name in ("measurements.json", "structure_calibrated.json")},
        "proposal_meta": {
            "title": "拓宽房门", "intent": "改善进出通行", "risk_codes": ["door_width"],
            "limitations": ["须现场复核"],
            "evidence": {"verification": {"source": "model", "comparison": {"after_score": 9999}},
                         "facts": {"score": -1234}},
        },
    }


def test_ai_scheme_preserves_narrative_and_recomputes_evidence(client, scheme_inputs):
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, *scheme_inputs)
    request = _ai_save_payload(sid)
    created = client.post(f"/api/assistant/scans/{sid}/schemes", json=request, headers=h)
    assert created.status_code == 200, created.text
    saved = created.json()
    meta = saved["proposal_meta"]
    assert meta["title"] == "拓宽房门"
    assert meta["intent"] == "改善进出通行"
    assert meta["limitations"] == ["须现场复核"]
    assert meta["risk_codes"] == ["door_width"]
    assert meta["evidence"]["design_intent"] == {"source": "model", "text": "改善进出通行"}
    assert meta["evidence"]["facts"]["source"] == "structured_scan"
    assert meta["evidence"]["facts"]["score"] != -1234
    verification = meta["evidence"]["verification"]
    assert verification["source"] == "build_scheme"
    assert verification["comparison"]["after_score"] == saved["score"]
    assert verification["comparison"]["after_score"] != 9999
    assert verification["improvements"] == [
        {"metric_code": "door_width", "before": 0.85, "after": 1.0, "unit": "m",
         "risk_before": "medium", "risk_after": "low"},
    ]
    door = next(item for item in verification["metric_changes"] if item["metric_code"] == "door_width")
    assert door["before"] == 0.85
    assert door["after"] == 1.0
    listed = client.get(f"/api/assistant/scans/{sid}/schemes", headers=h).json()["schemes"][0]
    detail = client.get(f"/api/assistant/scans/{sid}/schemes/{saved['id']}", headers=h).json()
    assert listed["proposal_meta"] == meta
    assert detail["proposal_meta"] == meta
    assert detail["plan"]["proposal_meta"] == meta
    assert detail["plan"]["based_on"] == request["based_on"]


def test_legacy_scheme_without_metadata_remains_readable(client, scheme_inputs):
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, *scheme_inputs)
    saved = client.post(f"/api/assistant/scans/{sid}/schemes", json={"name": "旧方案", "edits": []}, headers=h)
    assert saved.status_code == 200
    assert saved.json()["proposal_meta"] == {}
    listed = client.get(f"/api/assistant/scans/{sid}/schemes", headers=h).json()["schemes"][0]
    detail = client.get(f"/api/assistant/scans/{sid}/schemes/{saved.json()['id']}", headers=h).json()
    assert listed["proposal_meta"] == {}
    assert detail["proposal_meta"] == {}
    assert detail["plan"]["source"] == "manual"


@pytest.mark.parametrize("edits,reason", [
    ([], "invalid_edit"),
    ([{"op": "resize_door", "width_m": 0.5}], "risk_worsened"),
    ([{"op": "resize_door", "width_m": 1.0, "unexpected": True}], "invalid_edit"),
], ids=["empty_edits", "worsening_edit", "malformed_edit_schema"])
def test_ai_scheme_rejects_unverified_candidate_without_saving(client, scheme_inputs, edits, reason):
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, *scheme_inputs)
    request = _ai_save_payload(sid)
    request["edits"] = edits

    response = client.post(f"/api/assistant/scans/{sid}/schemes", json=request, headers=h)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["message"] == reason
    assert client.get(f"/api/assistant/scans/{sid}/schemes", headers=h).json()["schemes"] == []


def test_ai_scheme_rejects_structured_obstacle_overlap_without_saving(client, scheme_inputs):
    measurements, structure = scheme_inputs
    structure["geometric_obstacles"].append({
        "instance_id": "fixed_obstacle", "center": [1.2, 1.4, 0.2],
        "size": [0.4, 0.4, 0.4],
    })
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, measurements, structure)
    request = _ai_save_payload(sid)
    request["edits"] = [{"op": "move", "target": "desk_001", "dx": 0, "dy": -0.6}]

    response = client.post(f"/api/assistant/scans/{sid}/schemes", json=request, headers=h)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["message"] == "geometry_conflict"
    assert client.get(f"/api/assistant/scans/{sid}/schemes", headers=h).json()["schemes"] == []


def test_metadata_free_ai_scheme_keeps_legacy_save_behavior(client, scheme_inputs):
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, *scheme_inputs)
    response = client.post(f"/api/assistant/scans/{sid}/schemes",
                           json={"name": "旧 AI 方案", "source": "ai", "edits": []}, headers=h)
    assert response.status_code == 200, response.text
    assert response.json()["proposal_meta"] == {}


@pytest.mark.parametrize("change", ["changed", "missing", "extra"])
def test_scheme_rejects_stale_or_incomplete_fingerprints(client, scheme_inputs, change):
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, *scheme_inputs)
    request = _ai_save_payload(sid)
    if change == "changed":
        request["based_on"]["measurements_sha256"] = "0" * 64
    elif change == "missing":
        request["based_on"].pop("structure_calibrated_sha256")
    else:
        request["based_on"]["structure_sha256"] = "0" * 64
    response = client.post(f"/api/assistant/scans/{sid}/schemes", json=request, headers=h)
    assert response.status_code == 409, response.text
    assert client.get(f"/api/assistant/scans/{sid}/schemes", headers=h).json()["schemes"] == []


@pytest.mark.parametrize("field,value", [
    ("title", "长" * 121), ("intent", "长" * 601),
    ("limitations", ["长" * 601]), ("limitations", ["限制"] * 11),
    ("risk_codes", ["door_width"] * 21),
], ids=["title", "intent", "long_limitation", "too_many_limitations", "too_many_risks"])
def test_ai_scheme_rejects_overlong_narrative(client, scheme_inputs, field, value):
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, *scheme_inputs)
    request = _ai_save_payload(sid)
    request["proposal_meta"][field] = value
    response = client.post(f"/api/assistant/scans/{sid}/schemes", json=request, headers=h)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("change", ["missing_fingerprints", "wrong_source", "unknown_risk"])
def test_ai_scheme_requires_explicit_provenance(client, scheme_inputs, change):
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, *scheme_inputs)
    request = _ai_save_payload(sid)
    if change == "missing_fingerprints":
        request.pop("based_on")
    elif change == "wrong_source":
        request["source"] = "manual"
    else:
        request["proposal_meta"]["risk_codes"] = ["invented_risk"]
    response = client.post(f"/api/assistant/scans/{sid}/schemes", json=request, headers=h)
    assert response.status_code == 422, response.text


def test_ai_scheme_html_is_stored_as_untrusted_text(client, scheme_inputs):
    h = _auth(client)
    sid = _scan_with_artifacts(client, h, *scheme_inputs)
    request = _ai_save_payload(sid)
    html = '<img src=x onerror="alert(1)">'
    request["proposal_meta"]["intent"] = html
    request["proposal_meta"]["provider_secret"] = "must-not-persist"
    saved = client.post(f"/api/assistant/scans/{sid}/schemes", json=request, headers=h)
    assert saved.status_code == 200, saved.text
    detail = client.get(f"/api/assistant/scans/{sid}/schemes/{saved.json()['id']}", headers=h)
    assert detail.headers["content-type"] == "application/json"
    assert detail.json()["proposal_meta"]["intent"] == html
    assert detail.json()["proposal_meta"]["evidence"]["design_intent"]["text"] == html
    assert "provider_secret" not in detail.json()["proposal_meta"]


def test_proposal_metadata_migration_keeps_existing_scheme_readable(monkeypatch):
    import os

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.models import RenovationScheme

    database = Path("tmp") / f"assistant-migration-test-{uuid4().hex}.db"
    database.parent.mkdir(exist_ok=True)
    database_url = f"sqlite:///{database.as_posix()}"
    old_url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    migrated_engine = create_engine(database_url)
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "20260926_0005")
        with migrated_engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO organizations (id, name) VALUES (1, '历史机构')")
            connection.exec_driver_sql("INSERT INTO projects (id, org_id, name) VALUES (1, 1, '历史项目')")
            connection.exec_driver_sql("INSERT INTO scans (id, project_id) VALUES (1, 1)")
            connection.exec_driver_sql("INSERT INTO renovation_schemes (id, scan_id, name) VALUES (1, 1, '历史方案')")
        command.upgrade(config, "head")
        with Session(migrated_engine) as session:
            scheme = session.scalar(select(RenovationScheme))
            assert scheme.name == "历史方案"
            assert scheme.edits == []
            assert scheme.proposal_meta == {}
    finally:
        migrated_engine.dispose()
        monkeypatch.setenv("DATABASE_URL", old_url)
        get_settings.cache_clear()
