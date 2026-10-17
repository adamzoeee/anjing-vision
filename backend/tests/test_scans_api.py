from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Scan
from app.tasks.pipeline_tasks import TaskDispatchError
import pytest


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


def test_single_bed_length_reference_is_accepted(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "单参考标定"}, headers=h).json()["id"]
    sid = client.post(
        f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h
    ).json()["id"]

    response = client.put(
        f"/api/scans/{sid}/references",
        headers=h,
        json={"measurements": [
            {"object_type": "bed", "dimension": "length", "meters": 2.0},
        ]},
    )

    assert response.status_code == 200
    assert response.json()["reference_measurements"] == [
        {"object_type": "bed", "dimension": "length", "meters": 2.0}
    ]


def test_named_reference_is_normalized_and_saved(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "自由参考名称"}, headers=h).json()["id"]
    sid = client.post(
        f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h
    ).json()["id"]

    response = client.put(
        f"/api/scans/{sid}/references",
        headers=h,
        json={"measurements": [
            {"name": "床长度", "meters": 2.05},
        ]},
    )

    assert response.status_code == 200
    saved = response.json()["reference_measurements"][0]
    assert saved["object_type"] == "bed"
    assert saved["dimension"] == "length"
    assert saved["name"] == "床长度"
    assert saved["meters"] == 2.05


def test_unknown_named_reference_is_rejected_with_readable_error(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "未知参考名称"}, headers=h).json()["id"]
    sid = client.post(
        f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h
    ).json()["id"]

    response = client.put(
        f"/api/scans/{sid}/references",
        headers=h,
        json={"measurements": [{"name": "电视柜对角线", "meters": 2.05}]},
    )

    assert response.status_code == 422
    assert "无法识别参考项" in response.text
    assert "床长度" in response.text


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


def test_upload_over_limit_rejected(client, monkeypatch):
    """超过配置的上限返回 413。"""
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    # 用小上限验证逻辑，避免测试中构造数 GB 的超大文件。
    from app.routers import scans as scans_mod
    monkeypatch.setattr(scans_mod, "MAX_UPLOAD_BYTES", 100)
    r = client.post(f"/api/scans/{sid}/upload", headers=h,
                    files={"files": ("big.mp4", b"x" * 101, "video/mp4")})
    assert r.status_code == 413


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


def test_new_video_waits_for_explicit_start_and_preserves_old_result(client, monkeypatch):
    from app.routers import scans as scans_module
    dispatched = []
    monkeypatch.setattr(scans_module, "dispatch_scan", dispatched.append)
    h = _auth(client)
    pid = client.post('/api/projects', headers=h, json={'name': '重传'}).json()['id']
    old = client.post(f'/api/projects/{pid}/scans', headers=h, json={'capture_type': 'video'}).json()['id']
    with SessionLocal() as db:
        scan = db.get(Scan, old)
        scan.status, scan.media_path = 'done', 'media/old.mp4'
        db.commit()
    new = client.post(f'/api/projects/{pid}/scans', headers=h, json={'capture_type': 'video'}).json()['id']
    assert client.post(f'/api/scans/{new}/start', headers=h).status_code == 409
    uploaded = client.post(f'/api/scans/{new}/upload?defer_processing=true', headers=h,
                          files={'files': ('new.mp4', b'new-video', 'video/mp4')})
    assert uploaded.status_code == 200
    assert dispatched == []
    assert client.get(f'/api/scans/{new}', headers=h).json()['status'] == 'uploaded'
    assert client.post(f'/api/scans/{new}/start', headers=h).status_code == 200
    assert dispatched == [new]
    assert client.post(f'/api/scans/{new}/start', headers=h).status_code == 409
    assert dispatched == [new]
    unchanged = client.get(f'/api/scans/{old}', headers=h).json()
    assert unchanged['status'] == 'done'
    with SessionLocal() as db:
        assert db.get(Scan, old).media_path == 'media/old.mp4'
    assert client.post(f'/api/scans/{old}/upload?defer_processing=true', headers=h,
                       files={'files': ('new.mp4', b'x', 'video/mp4')}).status_code == 409


def test_deferred_start_queue_failure_keeps_video_for_retry(client, monkeypatch):
    from app.routers import scans as scans_module
    h = _auth(client)
    pid = client.post('/api/projects', headers=h, json={'name': '重试'}).json()['id']
    sid = client.post(f'/api/projects/{pid}/scans', headers=h, json={'capture_type': 'video'}).json()['id']
    monkeypatch.setattr(scans_module, 'dispatch_scan', lambda _: None)
    client.post(f'/api/scans/{sid}/upload?defer_processing=true', headers=h,
                files={'files': ('new.mp4', b'x', 'video/mp4')})
    def fail(_):
        raise TaskDispatchError('private broker password')
    monkeypatch.setattr(scans_module, 'dispatch_scan', fail)
    result = client.post(f'/api/scans/{sid}/start', headers=h)
    assert result.status_code == 503
    assert 'private' not in result.text
    assert client.get(f'/api/scans/{sid}', headers=h).json()['status'] == 'uploaded'
    monkeypatch.setattr(scans_module, 'dispatch_scan', lambda _: None)
    assert client.post(f'/api/scans/{sid}/start', headers=h).status_code == 200


@pytest.mark.parametrize('role,own,status', [('member', True, 403), ('admin', False, 404)])
def test_start_enforces_role_and_organization(client, role, own, status):
    from app.models import User, Project
    h = _auth(client)
    with SessionLocal() as db:
        user = db.query(User).filter_by(email='a1@x.com').one()
        project = Project(org_id=user.org_id, name='待开始')
        db.add(project); db.flush()
        scan = Scan(project_id=project.id, status='uploaded', media_path='media/new.mp4')
        db.add(scan); db.flush(); sid = scan.id
        if own:
            user.role = role
        db.commit()
    if not own:
        client.post('/api/auth/register', json={'org_name': '其他机构', 'name': '乙', 'email': 'other@x.com', 'password': 'secret123'})
        token = client.post('/api/auth/login', json={'email': 'other@x.com', 'password': 'secret123'}).json()['token']
        h = {'Authorization': f'Bearer {token}'}
    assert client.post(f'/api/scans/{sid}/start', headers=h).status_code == status


@pytest.mark.parametrize('filename,content', [('notes.txt', b'not a video'), ('empty.mp4', b'')])
def test_deferred_video_upload_requires_nonempty_video_file(client, monkeypatch, filename, content):
    from app.routers import scans as scans_module
    monkeypatch.setattr(scans_module, 'dispatch_scan', lambda _: None)
    h = _auth(client)
    pid = client.post('/api/projects', headers=h, json={'name': '校验新视频'}).json()['id']
    sid = client.post(f'/api/projects/{pid}/scans', headers=h, json={'capture_type': 'video'}).json()['id']
    response = client.post(f'/api/scans/{sid}/upload?defer_processing=true', headers=h,
                           files={'files': (filename, content, 'application/octet-stream')})
    assert response.status_code == 400
    assert client.get(f'/api/scans/{sid}', headers=h).json()['status'] == 'uploading'
    assert client.post(f'/api/scans/{sid}/start', headers=h).status_code == 409


def test_reupload_mode_survives_closing_dialog_and_resuming_upload(client, monkeypatch):
    from app.routers import scans as scans_module
    dispatched = []
    monkeypatch.setattr(scans_module, 'dispatch_scan', dispatched.append)
    h = _auth(client)
    pid = client.post('/api/projects', headers=h, json={'name': '恢复重传'}).json()['id']
    created = client.post(f'/api/projects/{pid}/scans', headers=h,
                          json={'capture_type': 'video', 'defer_processing': True}).json()
    assert created['status'] == 'awaiting_video'
    sid = created['id']
    refs = client.put(f'/api/scans/{sid}/references', headers=h, json={'measurements': [
        {'object_type': 'door', 'dimension': 'width', 'meters': .85},
        {'object_type': 'door', 'dimension': 'height', 'meters': 2.0},
    ]})
    assert refs.status_code == 200
    # A later session uses the default upload URL: the server must retain the explicit-start mode.
    result = client.post(f'/api/scans/{sid}/upload', headers=h,
                         files={'files': ('new.mp4', b'new-video', 'video/mp4')})
    assert result.status_code == 200
    assert dispatched == []
    assert client.get(f'/api/scans/{sid}', headers=h).json()['status'] == 'uploaded'
