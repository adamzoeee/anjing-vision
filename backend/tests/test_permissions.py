"""角色权限边界：成员只读，写操作统一由机构管理员执行；机构隔离不放松。"""

REFERENCE_BODY = {
    "measurements": [
        {"object_type": "door", "dimension": "width", "meters": 0.9},
        {"object_type": "bed", "dimension": "length", "meters": 2.0},
    ]
}

HOUSE_BODY = {
    "scan_ids": [1, 2],
    "links": [{"scan_a": 1, "door_a": "door_01", "scan_b": 2, "door_b": "door_01"}],
}

SCHEME_BODY = {"name": "改造方案", "edits": []}


def _setup(client):
    """管理员建项目 + 房间，再建一个成员账号，返回两套请求头。"""
    registered = client.post("/api/auth/register", json={
        "org_name": "养老院A", "name": "管理员", "email": "a1@x.com", "password": "secret123"})
    admin = {"Authorization": f"Bearer {registered.json()['token']}"}
    project = client.post("/api/projects", json={"name": "李奶奶家"}, headers=admin).json()
    scan = client.post(
        f"/api/projects/{project['id']}/scans", json={"capture_type": "video"}, headers=admin).json()
    client.post("/api/users", json={
        "name": "成员", "email": "m1@x.com", "password": "secret123"}, headers=admin)
    login = client.post("/api/auth/login", json={"email": "m1@x.com", "password": "secret123"})
    member = {"Authorization": f"Bearer {login.json()['token']}"}
    return admin, member, project, scan


def test_member_can_read_projects_and_scans(client):
    _, member, project, scan = _setup(client)
    assert client.get("/api/projects", headers=member).status_code == 200
    assert client.get(f"/api/projects/{project['id']}", headers=member).status_code == 200
    assert client.get(f"/api/projects/{project['id']}/scans", headers=member).status_code == 200
    assert client.get(f"/api/scans/{scan['id']}", headers=member).status_code == 200
    assert client.get(
        f"/api/assistant/scans/{scan['id']}/suggestions", headers=member).status_code != 403


def test_member_cannot_create_projects_or_scans(client):
    _, member, project, scan = _setup(client)
    assert client.post("/api/projects", json={"name": "新项目"}, headers=member).status_code == 403
    assert client.post(
        f"/api/projects/{project['id']}/scans",
        json={"capture_type": "video"}, headers=member).status_code == 403
    assert client.post(
        f"/api/projects/{project['id']}/scans/house",
        json=HOUSE_BODY, headers=member).status_code == 403
    assert client.post(
        f"/api/projects/{project['id']}/scans/house-demo", headers=member).status_code == 403


def test_member_cannot_upload_or_set_references(client):
    _, member, _, scan = _setup(client)
    upload = client.post(
        f"/api/scans/{scan['id']}/upload",
        files={"files": ("room.mp4", b"fake-video", "video/mp4")},
        headers=member,
    )
    assert upload.status_code == 403
    references = client.put(f"/api/scans/{scan['id']}/references", json=REFERENCE_BODY, headers=member)
    assert references.status_code == 403


def test_member_cannot_write_schemes(client):
    _, member, _, scan = _setup(client)
    assert client.post(
        f"/api/assistant/scans/{scan['id']}/schemes", json=SCHEME_BODY, headers=member).status_code == 403
    assert client.patch(
        f"/api/assistant/scans/{scan['id']}/schemes/1",
        json={"name": "改名"}, headers=member).status_code == 403
    assert client.delete(
        f"/api/assistant/scans/{scan['id']}/schemes/1", headers=member).status_code == 403
    assert client.post(
        f"/api/assistant/scans/{scan['id']}/schemes/1/export.pdf",
        files={"plan": ("plan.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        headers=member,
    ).status_code == 403


def test_admin_write_paths_are_not_blocked_by_role(client):
    """角色校验不误伤管理员：资源不存在时是 404，而不是 403。"""
    admin, _, project, _ = _setup(client)
    assert client.post("/api/projects", json={"name": "第二个项目"}, headers=admin).status_code == 200
    assert client.get("/api/projects/9999", headers=admin).status_code == 404
    assert client.get("/api/projects/9999/scans", headers=admin).status_code == 404
    assert client.put(
        "/api/scans/9999/references", json=REFERENCE_BODY, headers=admin).status_code == 404
