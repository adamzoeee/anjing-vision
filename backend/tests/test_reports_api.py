"""reports API 测试：报告查询与跨机构对比越权防护。"""
import os
from pathlib import Path


def _auth(client, org="养老院A", email="a1@x.com"):
    client.post("/api/auth/register", json={
        "org_name": org, "name": "甲", "email": email, "password": "secret123"})
    r = client.post("/api/auth/login", json={"email": email, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _make_report(client, h, name="王奶奶家", project_id=None):
    """创建项目+扫描，直接向 DB 写入 Report 记录（管道需 GPU，这里绕过）。"""
    from app.db import SessionLocal
    from app.models import Report
    if project_id is None:
        pid = client.post("/api/projects", json={"name": name}, headers=h).json()["id"]
    else:
        pid = project_id
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    db = SessionLocal()
    rep = Report(scan_id=sid, score=62.5,
                 risks=[{"code": "door_width", "name": "门宽", "level": "red", "measure": 0.75}],
                 measures={"door_width_m": 0.75}, advice=["建议扩门"],
                 images=[], preview={}, calibrated=1)
    db.add(rep)
    db.commit()
    rid = rep.id  # commit 会 expire 属性，先保存主键
    db.close()
    return pid, sid, rid


def test_get_report(client):
    h = _auth(client)
    _pid, sid, _rid = _make_report(client, h)
    r = client.get(f"/api/reports/scans/{sid}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["score"] == 62.5
    assert body["risks"][0]["level"] == "red"
    assert body["calibrated"] == 1


def test_get_report_prefers_rebuilt_measurements_file(client):
    import json
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import Scan

    headers = _auth(client, email="fresh-measurements@x.com")
    _pid, scan_id, _rid = _make_report(client, headers)
    db = SessionLocal()
    scan = db.get(Scan, scan_id)
    scan.reference_measurements = [
        {"object_type": "bed", "dimension": "length", "meters": 2.05},
        {"object_type": "door", "dimension": "height", "meters": 2.10},
    ]
    db.commit()
    db.close()
    path = Path(get_settings().data_dir) / "work" / str(scan_id) / "postprocess" / "measurements.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "metric_scale_available": True,
        "scale": {"status": "metric_references", "forced_estimate": True},
        "room": {"length_m": 3.2, "width_m": 2.5, "height_m": 2.6},
    }), encoding="utf-8")

    body = client.get(f"/api/reports/scans/{scan_id}", headers=headers).json()
    assert body["calibrated"] == 3
    assert body["measures"]["measurements"]["room"]["length_m"] == 3.2
    assert len(body["measures"]["reference_measurements"]) == 2


def test_get_report_prefers_backend_formal_risk_assessment_file(client):
    import json
    from app.config import get_settings

    headers = _auth(client, email="fresh-risk@x.com")
    _pid, scan_id, _rid = _make_report(client, headers)
    post = Path(get_settings().data_dir) / "work" / str(scan_id) / "postprocess"
    post.mkdir(parents=True, exist_ok=True)
    formal = {
        "schema_version": "1.0", "official": True,
        "overall": {"status": "evaluated", "score": 78.5},
        "risks": [{
            "risk_code": "door_width_medium", "risk_type": "mobility",
            "risk_name": "门净宽风险", "metric_code": "door_width",
            "measured_value": 0.85, "unit": "m", "threshold": {},
            "position": {"object_id": "door_01"}, "risk_level": "medium",
            "confidence": 0.9, "reason": "threshold", "advice": "调整门口净宽",
            "assessment_status": "evaluated", "related_object_ids": ["door_01"],
            "related_path_id": None,
        }],
        "advice": ["调整门口净宽"],
    }
    (post / "risk_assessment.json").write_text(
        json.dumps(formal, ensure_ascii=False), encoding="utf-8",
    )
    body = client.get(f"/api/reports/scans/{scan_id}", headers=headers).json()
    assert body["score"] == 78.5
    assert body["risks"][0]["risk_level"] == "medium"
    assert body["advice"] == ["调整门口净宽"]
    assert body["measures"]["risk_assessment"]["official"] is True


def test_report_annotation_image_is_served_with_organization_auth(client, tmp_path):
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import Report

    headers = _auth(client)
    _pid, scan_id, report_id = _make_report(client, headers)
    image_path = (
        Path(get_settings().data_dir)
        / "work"
        / str(scan_id)
        / "images"
        / "view_0.png"
    )
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"annotation-image")
    db = SessionLocal()
    report = db.get(Report, report_id)
    report.images = [str(image_path)]
    db.commit()
    db.close()

    response = client.get(f"/api/reports/scans/{scan_id}", headers=headers)
    image_url = response.json()["images"][0]
    assert image_url == f"/static/{scan_id}/view_0.png"
    assert client.get(image_url).status_code == 401

    image_response = client.get(image_url, headers=headers)
    assert image_response.status_code == 200
    assert image_response.content == b"annotation-image"

    other_headers = _auth(client, org="养老院B", email="images-b@x.com")
    assert client.get(image_url, headers=other_headers).status_code == 404

    outside_path = tmp_path / "outside.png"
    outside_path.write_bytes(b"must-not-be-served")
    db = SessionLocal()
    report = db.get(Report, report_id)
    report.images = [str(outside_path)]
    db.commit()
    db.close()
    assert client.get(
        f"/static/{scan_id}/outside.png",
        headers=headers,
    ).status_code == 404


def test_preview_serves_gaussian_model_and_camera_poses_with_auth(client):
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import Report

    headers = _auth(client, email="preview@x.com")
    _pid, scan_id, report_id = _make_report(client, headers)
    preview_dir = Path(get_settings().data_dir) / "work" / str(scan_id) / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    (preview_dir / "scene_gaussian.ply").write_bytes(b"ply\n")
    (preview_dir / "cameras.json").write_text("[]", encoding="utf-8")
    db = SessionLocal()
    report = db.get(Report, report_id)
    report.preview = {
        "gaussian_ply": "scene_gaussian.ply",
        "cameras": "cameras.json",
    }
    db.commit()
    db.close()

    base = f"/static/{scan_id}/preview"
    assert client.get(f"{base}/scene_gaussian.ply", headers=headers).content == b"ply\n"
    assert client.get(f"{base}/cameras.json", headers=headers).json() == []
    assert client.get(f"{base}/not-allowed.ply", headers=headers).status_code == 404


def test_preview_serves_formal_metrics_and_risk_assessment_with_auth(client):
    import json
    from app.config import get_settings

    headers = _auth(client, email="formal-risk-preview@x.com")
    _pid, scan_id, _report_id = _make_report(client, headers)
    post = Path(get_settings().data_dir) / "work" / str(scan_id) / "postprocess"
    post.mkdir(parents=True, exist_ok=True)
    (post / "spatial_metrics.json").write_text(
        json.dumps({"schema_version": "1.0", "metrics": []}), encoding="utf-8",
    )
    (post / "risk_assessment.json").write_text(
        json.dumps({"schema_version": "1.0", "official": True}), encoding="utf-8",
    )
    metrics = client.get(f"/api/preview/{scan_id}/spatial-metrics.json", headers=headers)
    assessment = client.get(f"/api/preview/{scan_id}/risk-assessment.json", headers=headers)
    assert metrics.status_code == 200
    assert metrics.json()["schema_version"] == "1.0"
    assert assessment.status_code == 200
    assert assessment.json()["official"] is True

    other_headers = _auth(client, org="养老院B", email="formal-risk-other@x.com")
    assert client.get(
        f"/api/preview/{scan_id}/risk-assessment.json", headers=other_headers,
    ).status_code == 404


def test_get_report_requires_ownership(client):
    h = _auth(client)
    _pid, sid, _rid = _make_report(client, h)
    h2 = _auth(client, org="养老院B", email="b1@x.com")
    assert client.get(f"/api/reports/scans/{sid}", headers=h2).status_code == 404


def test_get_report_missing_404(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    assert client.get(f"/api/reports/scans/{sid}", headers=h).status_code == 404


def test_compare_same_project(client):
    h = _auth(client)
    pid, sid_a, _rid_a = _make_report(client, h, name="改造前")
    _pid2, sid_b, _rid_b = _make_report(client, h, name="改造后", project_id=pid)
    r = client.get(f"/api/reports/compare?a={sid_a}&b={sid_b}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["score_delta"] == 0.0


def test_compare_cross_org_rejected(client):
    h = _auth(client)
    _pid, sid_a, _rid_a = _make_report(client, h)
    h2 = _auth(client, org="养老院B", email="b1@x.com")
    _pid2, sid_b, _rid_b = _make_report(client, h2, name="乙的项目")
    # 跨机构对比（B 拿 A 的扫描 id + 自己的扫描 id）
    r = client.get(f"/api/reports/compare?a={sid_a}&b={sid_b}", headers=h2)
    assert r.status_code == 404
