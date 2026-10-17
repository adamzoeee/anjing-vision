from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.models import Report, RenovationScheme, Scan, User


def _auth(client):
    client.post("/api/auth/register", json={
        "org_name": "删除测试机构",
        "name": "管理员",
        "email": "delete-admin@x.com",
        "password": "secret123",
    })
    login = client.post("/api/auth/login", json={
        "email": "delete-admin@x.com",
        "password": "secret123",
    })
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_admin_deletes_empty_scan_and_only_its_artifacts(client):
    headers = _auth(client)
    project = client.post(
        "/api/projects",
        json={"name": "待清理项目"},
        headers=headers,
    ).json()
    scan = client.post(
        f"/api/projects/{project['id']}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()
    keep = client.post(
        f"/api/projects/{project['id']}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()

    data_dir = Path(get_settings().data_dir)
    media_dir = data_dir / "media" / str(scan["id"])
    work_dir = data_dir / "work" / str(scan["id"])
    keep_dir = data_dir / "work" / str(keep["id"])
    media_dir.mkdir(parents=True)
    work_dir.mkdir(parents=True)
    keep_dir.mkdir(parents=True)
    (media_dir / "partial.mp4").write_bytes(b"partial-video")
    (work_dir / "frames").mkdir()
    (work_dir / "frames" / "frame.jpg").write_bytes(b"frame")
    (keep_dir / "keep.txt").write_text("keep", encoding="utf-8")

    response = client.delete(f"/api/scans/{scan['id']}", headers=headers)

    assert response.status_code == 204
    assert client.get(f"/api/scans/{scan['id']}", headers=headers).status_code == 404
    assert not media_dir.exists()
    assert not work_dir.exists()
    assert (keep_dir / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_deleting_done_scan_cascades_report_scheme_and_all_artifacts(client):
    headers = _auth(client)
    project = client.post(
        "/api/projects",
        json={"name": "已完成 Scan"},
        headers=headers,
    ).json()
    scan = client.post(
        f"/api/projects/{project['id']}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()
    scan_id = scan["id"]

    data_dir = Path(get_settings().data_dir)
    for directory in (
        data_dir / "media" / str(scan_id),
        data_dir / "work" / str(scan_id) / "postprocess",
        data_dir / "reports" / str(scan_id),
    ):
        directory.mkdir(parents=True)
    (data_dir / "media" / str(scan_id) / "room.mp4").write_bytes(b"video")
    (data_dir / "work" / str(scan_id) / "postprocess" / "scene.ply").write_bytes(b"cloud")
    (data_dir / "reports" / str(scan_id) / "report.pdf").write_bytes(b"pdf")

    with SessionLocal() as db:
        stored_scan = db.get(Scan, scan_id)
        stored_scan.status = "done"
        stored_scan.progress = 100
        stored_scan.media_path = f"media/{scan_id}/room.mp4"
        user = db.query(User).filter_by(email="delete-admin@x.com").one()
        report = Report(scan_id=scan_id, score=88)
        scheme = RenovationScheme(
            scan_id=scan_id,
            name="保留方案",
            created_by=user.id,
        )
        db.add_all([report, scheme])
        db.commit()
        report_id = report.id
        scheme_id = scheme.id

    response = client.delete(f"/api/scans/{scan_id}", headers=headers)

    assert response.status_code == 204
    assert not (data_dir / "media" / str(scan_id)).exists()
    assert not (data_dir / "work" / str(scan_id)).exists()
    assert not (data_dir / "reports" / str(scan_id)).exists()
    with SessionLocal() as db:
        assert db.get(Scan, scan_id) is None
        assert db.get(Report, report_id) is None
        assert db.get(RenovationScheme, scheme_id) is None


def test_delete_enforces_role_org_and_active_state(client):
    headers = _auth(client)
    project = client.post(
        "/api/projects",
        json={"name": "删除权限"},
        headers=headers,
    ).json()
    scan = client.post(
        f"/api/projects/{project['id']}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()
    scan_id = scan["id"]

    client.post("/api/users", json={
        "name": "只读成员",
        "email": "delete-member@x.com",
        "password": "secret123",
    }, headers=headers)
    member_login = client.post("/api/auth/login", json={
        "email": "delete-member@x.com",
        "password": "secret123",
    })
    member = {"Authorization": f"Bearer {member_login.json()['token']}"}
    assert client.delete(f"/api/scans/{scan_id}", headers=member).status_code == 403

    client.post("/api/auth/register", json={
        "org_name": "其他删除机构",
        "name": "外部管理员",
        "email": "delete-outsider@x.com",
        "password": "secret123",
    })
    outsider_login = client.post("/api/auth/login", json={
        "email": "delete-outsider@x.com",
        "password": "secret123",
    })
    outsider = {"Authorization": f"Bearer {outsider_login.json()['token']}"}
    assert client.delete(f"/api/scans/{scan_id}", headers=outsider).status_code == 404

    with SessionLocal() as db:
        stored_scan = db.get(Scan, scan_id)
        stored_scan.status = "reconstructing"
        stored_scan.media_path = f"media/{scan_id}/room.mp4"
        db.commit()
    assert client.delete(f"/api/scans/{scan_id}", headers=headers).status_code == 409
    assert client.get(f"/api/scans/{scan_id}", headers=headers).status_code == 200
