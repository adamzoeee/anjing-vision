from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.models import Project, RenovationScheme, Report, Scan, User


def _auth(client):
    client.post("/api/auth/register", json={
        "org_name": "项目管理机构",
        "name": "管理员",
        "email": "project-admin@x.com",
        "password": "secret123",
    })
    login = client.post("/api/auth/login", json={
        "email": "project-admin@x.com",
        "password": "secret123",
    })
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_admin_renames_project_without_changing_address(client):
    headers = _auth(client)
    project = client.post(
        "/api/projects",
        json={"name": "旧名称", "address": "幸福路 1 号"},
        headers=headers,
    ).json()

    response = client.patch(
        f"/api/projects/{project['id']}",
        json={"name": "  新名称  "},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "新名称"
    assert response.json()["address"] == "幸福路 1 号"
    listed = client.get("/api/projects", headers=headers).json()
    assert listed[0]["name"] == "新名称"


def test_admin_deletes_project_with_all_scan_data(client):
    headers = _auth(client)
    project = client.post(
        "/api/projects",
        json={"name": "待删除项目", "address": "旧房"},
        headers=headers,
    ).json()
    other = client.post(
        "/api/projects",
        json={"name": "保留项目"},
        headers=headers,
    ).json()
    scans = [
        client.post(
            f"/api/projects/{project['id']}/scans",
            json={"capture_type": "video"},
            headers=headers,
        ).json()
        for _ in range(2)
    ]
    keep_scan = client.post(
        f"/api/projects/{other['id']}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()

    data_dir = Path(get_settings().data_dir)
    for scan in scans:
        scan_id = scan["id"]
        for directory in (
            data_dir / "media" / str(scan_id),
            data_dir / "work" / str(scan_id) / "postprocess",
            data_dir / "reports" / str(scan_id),
            data_dir / "cache" / "media" / str(scan_id),
        ):
            directory.mkdir(parents=True)
        (data_dir / "media" / str(scan_id) / "room.mp4").write_bytes(b"video")
        (data_dir / "work" / str(scan_id) / "postprocess" / "scene.ply").write_bytes(b"cloud")
    keep_dir = data_dir / "work" / str(keep_scan["id"])
    keep_dir.mkdir(parents=True)
    (keep_dir / "keep.txt").write_text("keep", encoding="utf-8")

    with SessionLocal() as db:
        user = db.query(User).filter_by(email="project-admin@x.com").one()
        for scan in scans:
            stored = db.get(Scan, scan["id"])
            stored.status = "done"
            stored.progress = 100
            stored.media_path = f"media/{scan['id']}/room.mp4"
        report = Report(scan_id=scans[0]["id"], score=88)
        scheme = RenovationScheme(
            scan_id=scans[0]["id"],
            name="项目方案",
            created_by=user.id,
        )
        db.add_all([report, scheme])
        db.commit()
        report_id = report.id
        scheme_id = scheme.id

    response = client.delete(f"/api/projects/{project['id']}", headers=headers)

    assert response.status_code == 204
    assert client.get(f"/api/projects/{project['id']}", headers=headers).status_code == 404
    for scan in scans:
        assert client.get(f"/api/scans/{scan['id']}", headers=headers).status_code == 404
        assert not (data_dir / "media" / str(scan["id"])).exists()
        assert not (data_dir / "work" / str(scan["id"])).exists()
        assert not (data_dir / "reports" / str(scan["id"])).exists()
        assert not (data_dir / "cache" / "media" / str(scan["id"])).exists()
    with SessionLocal() as db:
        assert db.get(Project, project["id"]) is None
        assert db.query(Scan).filter(Scan.project_id == project["id"]).count() == 0
        assert db.get(Report, report_id) is None
        assert db.get(RenovationScheme, scheme_id) is None
        assert db.get(Project, other["id"]) is not None
    assert (keep_dir / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_project_delete_is_blocked_while_scan_is_processing(client):
    headers = _auth(client)
    project = client.post(
        "/api/projects",
        json={"name": "处理中项目"},
        headers=headers,
    ).json()
    scan = client.post(
        f"/api/projects/{project['id']}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()
    with SessionLocal() as db:
        stored = db.get(Scan, scan["id"])
        stored.status = "reconstructing"
        stored.media_path = f"media/{scan['id']}/room.mp4"
        db.commit()
    work_dir = Path(get_settings().data_dir) / "work" / str(scan["id"])
    work_dir.mkdir(parents=True)
    (work_dir / "active.ply").write_bytes(b"active")

    response = client.delete(f"/api/projects/{project['id']}", headers=headers)

    assert response.status_code == 409
    assert client.get(f"/api/projects/{project['id']}", headers=headers).status_code == 200
    assert (work_dir / "active.ply").exists()


def test_project_manage_enforces_role_and_organization(client):
    headers = _auth(client)
    project = client.post(
        "/api/projects",
        json={"name": "权限项目"},
        headers=headers,
    ).json()
    client.post("/api/users", json={
        "name": "只读成员",
        "email": "project-member@x.com",
        "password": "secret123",
    }, headers=headers)
    member_login = client.post("/api/auth/login", json={
        "email": "project-member@x.com",
        "password": "secret123",
    })
    member = {"Authorization": f"Bearer {member_login.json()['token']}"}
    assert client.patch(
        f"/api/projects/{project['id']}",
        json={"name": "成员改名"},
        headers=member,
    ).status_code == 403
    assert client.delete(f"/api/projects/{project['id']}", headers=member).status_code == 403

    client.post("/api/auth/register", json={
        "org_name": "外部项目机构",
        "name": "外部管理员",
        "email": "project-outsider@x.com",
        "password": "secret123",
    })
    outsider_login = client.post("/api/auth/login", json={
        "email": "project-outsider@x.com",
        "password": "secret123",
    })
    outsider = {"Authorization": f"Bearer {outsider_login.json()['token']}"}
    assert client.patch(
        f"/api/projects/{project['id']}",
        json={"name": "越权改名"},
        headers=outsider,
    ).status_code == 404
    assert client.delete(f"/api/projects/{project['id']}", headers=outsider).status_code == 404
