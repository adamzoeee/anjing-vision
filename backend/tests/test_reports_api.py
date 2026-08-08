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
