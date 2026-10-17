def _register(client, org="幸福养老院", name="张护工", email="z@example.com", password="secret123"):
    return client.post("/api/auth/register", json={
        "org_name": org, "name": name, "email": email, "password": password,
    })


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_register_and_login(client):
    r = _register(client)
    assert r.status_code == 200, r.text
    assert r.json()["token"]
    r2 = client.post("/api/auth/login", json={"email": "z@example.com", "password": "secret123"})
    assert r2.status_code == 200
    r3 = client.get("/api/auth/me", headers=_headers(r2.json()["token"]))
    assert r3.json()["email"] == "z@example.com"
    assert r3.json()["org_name"] == "幸福养老院"


def test_wrong_password_rejected(client):
    _register(client, org="A", name="B", email="a@b.c")
    r = client.post("/api/auth/login", json={"email": "a@b.c", "password": "wrong"})
    assert r.status_code == 401


def test_unknown_email_rejected(client):
    r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "secret123"})
    assert r.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_duplicate_email_rejected(client):
    _register(client, org="A", name="B", email="a@b.c")
    r = _register(client, org="A", name="C", email="a@b.c", password="secret456")
    assert r.status_code == 400


def test_register_creates_admin_role(client):
    r = _register(client, org="新机构", name="甲", email="x1@x.com")
    assert r.json()["user"]["role"] == "admin"
    assert r.json()["user"]["is_active"] is True


def test_public_registration_cannot_join_existing_org(client):
    _register(client, org="新机构", name="甲", email="x1@x.com")
    second = _register(client, org="新机构", name="乙", email="x2@x.com")
    assert second.status_code == 409
    assert client.post("/api/auth/login", json={
        "email": "x2@x.com", "password": "secret123"}).status_code == 401


def test_public_registration_creates_separate_admin_org(client):
    first = _register(client, org="机构甲", email="first@example.com")
    second = _register(client, org="机构乙", email="second@example.com")
    assert first.json()["user"]["role"] == "admin"
    assert second.json()["user"]["role"] == "admin"
    assert first.json()["user"]["org_name"] != second.json()["user"]["org_name"]


def test_registration_rejects_password_over_bcrypt_byte_limit(client):
    response = _register(client, org="超长密码机构", password="密" * 25)
    assert response.status_code == 422


def test_demo_login_endpoint_no_longer_exists(client):
    """演示旁路已删除：登录只剩邮箱 + 密码一条路径。"""
    assert client.post("/api/auth/demo-login", json={}).status_code == 404
    assert client.post("/api/auth/demo-login", json={"email": "111111", "password": "随便输入"}).status_code == 404


def test_me_reports_role_and_active_state(client):
    token = _register(client).json()["token"]
    me = client.get("/api/auth/me", headers=_headers(token)).json()
    assert me["role"] == "admin"
    assert me["is_active"] is True


def test_password_change_requires_current_password(client):
    token = _register(client).json()["token"]
    headers = _headers(token)
    wrong = client.post("/api/auth/password", json={
        "current_password": "wrong-password", "new_password": "brandnew123"}, headers=headers)
    assert wrong.status_code == 400
    ok = client.post("/api/auth/password", json={
        "current_password": "secret123", "new_password": "brandnew123"}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.post("/api/auth/login", json={
        "email": "z@example.com", "password": "secret123"}).status_code == 401
    assert client.post("/api/auth/login", json={
        "email": "z@example.com", "password": "brandnew123"}).status_code == 200


def test_admin_password_reset_revokes_member_token(client):
    admin = _headers(_register(client).json()["token"])
    created = client.post("/api/users", json={
        "name": "成员", "email": "m@example.com", "password": "secret123"}, headers=admin)
    member = _headers(client.post("/api/auth/login", json={
        "email": "m@example.com", "password": "secret123"}).json()["token"])
    reset = client.post(f"/api/users/{created.json()['id']}/password", json={
        "password": "brandnew123"}, headers=admin)
    assert reset.status_code == 200
    assert client.get("/api/auth/me", headers=member).status_code == 401


def test_repeated_password_change_is_rejected(client):
    token = _register(client).json()["token"]
    r = client.post("/api/auth/password", json={
        "current_password": "secret123", "new_password": "secret123"}, headers=_headers(token))
    assert r.status_code == 400


def test_disabled_account_is_locked_out_immediately(client):
    admin = _headers(_register(client).json()["token"])
    created = client.post("/api/users", json={
        "name": "成员", "email": "m@example.com", "password": "secret123"}, headers=admin)
    member_id = created.json()["id"]
    member = _headers(client.post("/api/auth/login", json={
        "email": "m@example.com", "password": "secret123"}).json()["token"])
    assert client.get("/api/auth/me", headers=member).status_code == 200

    disabled = client.patch(f"/api/users/{member_id}", json={"is_active": False}, headers=admin)
    assert disabled.status_code == 200, disabled.text
    # 停用立即生效：已签发的 token 也不能再用
    assert client.get("/api/auth/me", headers=member).status_code == 401
    relogin = client.post("/api/auth/login", json={"email": "m@example.com", "password": "secret123"})
    assert relogin.status_code == 403
