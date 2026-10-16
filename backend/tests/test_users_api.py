"""机构成员账号管理：管理员建号 / 改名 / 重置密码 / 停用启用，角色与机构边界。"""


def _admin(client, org="养老院A", email="a1@x.com"):
    r = client.post("/api/auth/register", json={
        "org_name": org, "name": "管理员", "email": email, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _add_member(client, admin_headers, email="m1@x.com", password="secret123"):
    created = client.post("/api/users", json={
        "name": "成员", "email": email, "password": password}, headers=admin_headers)
    assert created.status_code == 201, created.text
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    return created.json(), {"Authorization": f"Bearer {login.json()['token']}"}


def test_admin_creates_and_lists_members(client):
    admin = _admin(client)
    body = client.post("/api/users", json={
        "name": "李护工", "email": "m1@x.com", "password": "secret123"}, headers=admin).json()
    assert body["role"] == "member"
    assert body["is_active"] is True
    assert body["disabled_at"] is None
    users = client.get("/api/users", headers=admin).json()
    assert [user["email"] for user in users] == ["a1@x.com", "m1@x.com"]
    assert users[0]["role"] == "admin"


def test_created_member_is_never_an_admin(client):
    """角色由服务端固定，客户端传入 role 也不生效（每机构只允许一个管理员）。"""
    admin = _admin(client)
    created = client.post("/api/users", json={
        "name": "成员", "email": "m1@x.com", "password": "secret123", "role": "admin"}, headers=admin)
    assert created.status_code == 201
    assert created.json()["role"] == "member"


def test_member_cannot_use_user_management(client):
    admin = _admin(client)
    _, member = _add_member(client, admin)
    assert client.get("/api/users", headers=member).status_code == 403
    assert client.post("/api/users", json={
        "name": "x", "email": "x@x.com", "password": "secret123"}, headers=member).status_code == 403


def test_admin_cannot_change_own_account(client):
    admin = _admin(client)
    me_id = client.get("/api/auth/me", headers=admin).json()["id"]
    assert client.patch(f"/api/users/{me_id}", json={"is_active": False}, headers=admin).status_code == 400
    assert client.post(f"/api/users/{me_id}/password", json={
        "password": "brandnew123"}, headers=admin).status_code == 400


def test_member_of_other_org_is_invisible(client):
    admin = _admin(client, org="养老院A", email="a1@x.com")
    other = _admin(client, org="养老院B", email="b1@x.com")
    other_id = client.get("/api/auth/me", headers=other).json()["id"]
    assert client.patch(f"/api/users/{other_id}", json={"is_active": False}, headers=admin).status_code == 404
    assert client.post(f"/api/users/{other_id}/password", json={
        "password": "brandnew123"}, headers=admin).status_code == 404
    assert [user["email"] for user in client.get("/api/users", headers=admin).json()] == ["a1@x.com"]


def test_duplicate_email_rejected_by_admin(client):
    admin = _admin(client)
    _add_member(client, admin, email="dup@x.com")
    again = client.post("/api/users", json={
        "name": "乙", "email": "dup@x.com", "password": "secret123"}, headers=admin)
    assert again.status_code == 400


def test_admin_can_rename_member(client):
    admin = _admin(client)
    member, _ = _add_member(client, admin)
    renamed = client.patch(f"/api/users/{member['id']}", json={"name": "李护工"}, headers=admin)
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "李护工"


def test_admin_resets_password_and_toggles_member(client):
    admin = _admin(client)
    member, _ = _add_member(client, admin)
    assert client.post(f"/api/users/{member['id']}/password", json={
        "password": "brandnew123"}, headers=admin).status_code == 200
    assert client.post("/api/auth/login", json={
        "email": "m1@x.com", "password": "secret123"}).status_code == 401
    assert client.post("/api/auth/login", json={
        "email": "m1@x.com", "password": "brandnew123"}).status_code == 200

    assert client.patch(f"/api/users/{member['id']}", json={"is_active": False}, headers=admin).status_code == 200
    assert client.post("/api/auth/login", json={
        "email": "m1@x.com", "password": "brandnew123"}).status_code == 403
    assert client.patch(f"/api/users/{member['id']}", json={"is_active": True}, headers=admin).status_code == 200
    assert client.post("/api/auth/login", json={
        "email": "m1@x.com", "password": "brandnew123"}).status_code == 200


def test_update_requires_at_least_one_field(client):
    admin = _admin(client)
    member, _ = _add_member(client, admin)
    assert client.patch(f"/api/users/{member['id']}", json={}, headers=admin).status_code == 422


def test_user_management_requires_login(client):
    assert client.get("/api/users").status_code == 401
