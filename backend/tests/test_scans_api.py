from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Scan
from app.tasks.pipeline_tasks import TaskDispatchError


def _auth(client):
    client.post("/api/auth/register", json={
        "org_name": "养老院A", "name": "甲", "email": "a1@x.com", "password": "secret123"})
    r = client.post("/api/auth/login", json={"email": "a1@x.com", "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_create_scan_and_upload_video(client, tmp_path, monkeypatch):
    import app.routers.scans as scans_module
    called = {}
    monkeypatch.setattr(scans_module, "dispatch_scan",
                        lambda scan_id: called.setdefault("id", scan_id))
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    r = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h)
    assert r.status_code == 200
    sid = r.json()["id"]
    assert r.json()["status"] == "uploading"
    fake = tmp_path / "clip.mp4"
    fake.write_bytes(b"fake-mp4-content")
    r = client.post(
        f"/api/scans/{sid}/upload", headers=h,
        files={"files": ("clip.mp4", fake.read_bytes(), "video/mp4")},
    )
    assert r.status_code == 200
    assert called.get("id") == sid  # 上传完成即触发管道分发
    r = client.get(f"/api/scans/{sid}", headers=h)
    assert r.json()["status"] == "uploading"  # 管道异步推进，不阻塞上传请求


def test_scan_ownership_enforced(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    client.post("/api/auth/register", json={
        "org_name": "养老院B", "name": "乙", "email": "b1@x.com", "password": "secret123"})
    r2 = client.post("/api/auth/login", json={"email": "b1@x.com", "password": "secret123"})
    h2 = {"Authorization": f"Bearer {r2.json()['token']}"}
    assert client.get(f"/api/scans/{sid}", headers=h2).status_code == 404


def test_reference_measurements_are_saved_before_upload(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "尺寸标定房间"}, headers=h).json()["id"]
    sid = client.post(
        f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h
    ).json()["id"]
    response = client.put(
        f"/api/scans/{sid}/references",
        headers=h,
        json={"measurements": [
            {"object_type": "door", "dimension": "height", "meters": 2.05},
            {"object_type": "bed", "dimension": "length", "meters": 2.0},
        ]},
    )
    assert response.status_code == 200
    assert response.json()["reference_measurements"][0]["meters"] == 2.05


def test_reference_measurements_validate_bounds(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "非法尺寸"}, headers=h).json()["id"]
    sid = client.post(
        f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h
    ).json()["id"]
    response = client.put(
        f"/api/scans/{sid}/references",
        headers=h,
        json={"measurements": [
            {"object_type": "door", "dimension": "height", "meters": 0.01},
        ]},
    )
    assert response.status_code == 422


def test_bookshelf_can_be_used_as_metric_reference(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "书架标定"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    response = client.put(
        f"/api/scans/{sid}/references",
        headers=h,
        json={"measurements": [
            {"object_type": "bookshelf", "dimension": "height", "meters": 1.8},
            {"object_type": "bed", "dimension": "length", "meters": 2.0},
        ]},
    )
    assert response.status_code == 200


def test_reference_measurements_require_two_distinct_supported_dimensions(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "重复尺寸"}, headers=h).json()["id"]
    sid = client.post(
        f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h
    ).json()["id"]
    duplicate = {"object_type": "door", "dimension": "height", "meters": 2.05}
    response = client.put(
        f"/api/scans/{sid}/references",
        headers=h,
        json={"measurements": [duplicate, duplicate]},
    )
    assert response.status_code == 422

    unsupported = client.put(
        f"/api/scans/{sid}/references",
        headers=h,
        json={"measurements": [
            {"object_type": "wall", "dimension": "length", "meters": 3.5},
            {"object_type": "bed", "dimension": "length", "meters": 2.0},
        ]},
    )
    assert unsupported.status_code == 422


def test_upload_rejects_missing_and_cross_organization_scan(client):
    h = _auth(client)
    missing = client.post(
        "/api/scans/999999/upload",
        headers=h,
        files={"files": ("small.bin", b"x", "application/octet-stream")},
    )

    client.post("/api/auth/register", json={
        "org_name": "养老院B", "name": "乙", "email": "b1@x.com", "password": "secret123"})
    login = client.post(
        "/api/auth/login",
        json={"email": "b1@x.com", "password": "secret123"},
    )
    outsider = {"Authorization": f"Bearer {login.json()['token']}"}
    project_id = client.post(
        "/api/projects",
        json={"name": "乙的项目"},
        headers=outsider,
    ).json()["id"]
    scan_id = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "video"},
        headers=outsider,
    ).json()["id"]
    cross_org = client.post(
        f"/api/scans/{scan_id}/upload",
        headers=h,
        files={"files": ("small.bin", b"x", "application/octet-stream")},
    )

    assert missing.status_code == 404
    assert cross_org.status_code == 404


def test_video_mode_rejects_multiple_files(client):
    h = _auth(client)
    project_id = client.post(
        "/api/projects",
        json={"name": "单视频限制"},
        headers=h,
    ).json()["id"]
    scan_id = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "video"},
        headers=h,
    ).json()["id"]

    response = client.post(
        f"/api/scans/{scan_id}/upload",
        headers=h,
        files=[
            ("files", ("one.bin", b"1", "application/octet-stream")),
            ("files", ("two.bin", b"2", "application/octet-stream")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "视频模式仅接受单个文件"


def test_upload_save_failure_rolls_back_database_state(client, monkeypatch):
    from app.routers import scans as scans_module

    h = _auth(client)
    project_id = client.post(
        "/api/projects",
        json={"name": "保存失败"},
        headers=h,
    ).json()["id"]
    scan_id = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "video"},
        headers=h,
    ).json()["id"]

    def fail_save(*_args, **_kwargs):
        raise OSError("disk path and secret must not leak")

    monkeypatch.setattr(scans_module, "save_media_stream", fail_save)
    with TestClient(app, raise_server_exceptions=False) as safe_client:
        response = safe_client.post(
            f"/api/scans/{scan_id}/upload",
            headers=h,
            files={"files": ("small.bin", b"x", "application/octet-stream")},
        )

    db = SessionLocal()
    try:
        scan = db.get(Scan, scan_id)
        assert scan.media_path == ""
        assert scan.status == "uploading"
    finally:
        db.close()

    assert response.status_code == 500
    assert response.json()["error"]["message"] == "服务器内部错误"
    assert "disk path" not in response.text


def test_dispatch_failure_marks_scan_failed_without_leaking_error(client, monkeypatch):
    from app.routers import scans as scans_module

    h = _auth(client)
    project_id = client.post(
        "/api/projects",
        json={"name": "队列失败"},
        headers=h,
    ).json()["id"]
    scan_id = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "video"},
        headers=h,
    ).json()["id"]

    def fail_dispatch(_):
        raise TaskDispatchError("broker password=hidden")

    monkeypatch.setattr(scans_module, "dispatch_scan", fail_dispatch)
    response = client.post(
        f"/api/scans/{scan_id}/upload",
        headers=h,
        files={"files": ("small.bin", b"x", "application/octet-stream")},
    )
    status = client.get(f"/api/scans/{scan_id}", headers=h)

    assert response.status_code == 200
    assert status.json()["status"] == "failed"
    assert status.json()["message"] == "任务队列暂不可用，请稍后重试"
    assert "password=hidden" not in status.text


def test_upload_sanitizes_filename(client, tmp_path, monkeypatch):
    """路径注入防护：../ 与空 basename 均不能逃逸 media 目录或返回 500。"""
    import app.routers.scans as scans_module
    monkeypatch.setattr(scans_module, "dispatch_scan", lambda scan_id: None)
    import os
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    # ../ 文件名 → 落盘路径不含 ..
    r = client.post(f"/api/scans/{sid}/upload", headers=h,
                    files={"files": ("../../evil.mp4", b"x", "video/mp4")})
    assert r.status_code == 200
    media = r.json()["media"]
    assert ".." not in media
    # 空 basename（如 ".."）→ 兜底 media 前缀，不抛 500
    r2 = client.post(f"/api/scans/{sid}/upload", headers=h,
                     files={"files": ("..", b"x", "video/mp4")})
    assert r2.status_code == 200
    assert r2.json()["media"].split("/")[-1].startswith("media")


def test_upload_over_limit_rejected(client):
    """超过 512MB 上限返回 413。"""
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    # 用小上限验证逻辑：直接构造超限数据（512MB+1 太大，用 monkeypatch 缩小常量）
    from app.routers import scans as scans_mod
    scans_mod.MAX_UPLOAD_BYTES = 100
    r = client.post(f"/api/scans/{sid}/upload", headers=h,
                    files={"files": ("big.mp4", b"x" * 101, "video/mp4")})
    assert r.status_code == 413
    scans_mod.MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def test_photos_upload_multiple_files(client, tmp_path, monkeypatch):
    """照片模式：多文件上传，media_path 指向目录。"""
    import app.routers.scans as scans_module
    monkeypatch.setattr(scans_module, "dispatch_scan", lambda scan_id: None)
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "photos"}, headers=h).json()["id"]
    files = [
        ("files", ("p1.jpg", b"img1", "image/jpeg")),
        ("files", ("p2.jpg", b"img2", "image/jpeg")),
        ("files", ("p3.jpg", b"img3", "image/jpeg")),
    ]
    r = client.post(f"/api/scans/{sid}/upload", headers=h, files=files)
    assert r.status_code == 200, r.text
    media = r.json()["media"]
    assert media == f"media/{sid}"  # 目录路径 → pipeline_runner is_dir() 分支
    # 文件确实落盘
    from app.storage import media_path
    assert (media_path(media) / "p1.jpg").exists()


def test_list_scans_by_project(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h)
    client.post(f"/api/projects/{pid}/scans", json={"capture_type": "photos"}, headers=h)
    r = client.get(f"/api/projects/{pid}/scans", headers=h)
    assert len(r.json()) == 2
