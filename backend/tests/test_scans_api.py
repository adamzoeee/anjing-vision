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


def test_list_scans_by_project(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    client.post(f"/api/projects/{pid}/scans", json={"capture_type": "video"}, headers=h)
    client.post(f"/api/projects/{pid}/scans", json={"capture_type": "photos"}, headers=h)
    r = client.get(f"/api/projects/{pid}/scans", headers=h)
    assert len(r.json()) == 2
