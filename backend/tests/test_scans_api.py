def _auth(client):
    client.post("/api/auth/register", json={
        "org_name": "养老院A", "name": "甲", "email": "a1@x.com", "password": "secret123"})
    r = client.post("/api/auth/login", json={"email": "a1@x.com", "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_create_scan_and_upload_video(client, tmp_path):
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
        files={"file": ("clip.mp4", fake.read_bytes(), "video/mp4")},
    )
    assert r.status_code == 200
    r = client.get(f"/api/scans/{sid}", headers=h)
    assert r.json()["status"] != "uploading"  # 上传后进入管道（同步模式会失败但状态已流转）


def test_scan_ownership_enforced(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    client.post("/api/auth/register", json={
        "org_name": "养老院B", "name": "乙", "email": "b1@x.com", "password": "secret123"})
    r2 = client.post("/api/auth/login", json={"email": "b1@x.com", "password": "secret123"})
    h2 = {"Authorization": f"Bearer {r2.json()['token']}"}
    assert client.get(f"/api/scans/{sid}", headers=h2).status_code == 404


def test_upload_sanitizes_filename(client, tmp_path):
    """路径注入防护：../ 与空 basename 均不能逃逸 media 目录或返回 500。"""
    import os
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    # ../ 文件名 → 落盘路径不含 ..
    r = client.post(f"/api/scans/{sid}/upload", headers=h,
                    files={"file": ("../../evil.mp4", b"x", "video/mp4")})
    assert r.status_code == 200
    media = r.json()["media"]
    assert ".." not in media
    # 空 basename（如 ".."）→ 兜底 media.bin，不抛 500
    r2 = client.post(f"/api/scans/{sid}/upload", headers=h,
                     files={"file": ("..", b"x", "video/mp4")})
    assert r2.status_code == 200
    assert r2.json()["media"].endswith("media.bin")


def test_upload_over_limit_rejected(client):
    """超过 512MB 上限返回 413。"""
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h).json()["id"]
    # 用小上限验证逻辑：直接构造超限数据（512MB+1 太大，用 monkeypatch 缩小常量）
    from app.routers import scans as scans_mod
    scans_mod.MAX_UPLOAD_BYTES = 100
    r = client.post(f"/api/scans/{sid}/upload", headers=h,
                    files={"file": ("big.mp4", b"x" * 101, "video/mp4")})
    assert r.status_code == 413
    scans_mod.MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def test_list_scans_by_project(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h)
    client.post(f"/api/projects/{pid}/scans", json={"capture_type": "photos"}, headers=h)
    r = client.get(f"/api/projects/{pid}/scans", headers=h)
    assert len(r.json()) == 2
