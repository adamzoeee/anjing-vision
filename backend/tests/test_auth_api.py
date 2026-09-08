from app.config import Settings, get_settings


def test_register_and_login(client):
    r = client.post("/api/auth/register", json={
        "org_name": "幸福养老院", "name": "张护工", "email": "z@example.com", "password": "secret123",
    })
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert token
    r2 = client.post("/api/auth/login", json={"email": "z@example.com", "password": "secret123"})
    assert r2.status_code == 200
    r3 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r2.json()['token']}"})
    assert r3.json()["email"] == "z@example.com"
    assert r3.json()["org_name"] == "幸福养老院"


def test_wrong_password_rejected(client):
    client.post("/api/auth/register", json={
        "org_name": "A", "name": "B", "email": "a@b.c", "password": "secret123",
    })
    r = client.post("/api/auth/login", json={"email": "a@b.c", "password": "wrong"})
    assert r.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_duplicate_email_rejected(client):
    client.post("/api/auth/register", json={
        "org_name": "A", "name": "B", "email": "a@b.c", "password": "secret123"})
    r = client.post("/api/auth/register", json={
        "org_name": "A", "name": "C", "email": "a@b.c", "password": "secret456"})
    assert r.status_code == 400


def test_register_creates_admin_role(client):
    r = client.post("/api/auth/register", json={
        "org_name": "新机构", "name": "甲", "email": "x1@x.com", "password": "secret123"})
    assert r.json()["user"]["role"] == "admin"


def test_demo_login_accepts_arbitrary_text_and_returns_existing_account(client):
    registered = client.post("/api/auth/register", json={
        "org_name": "演示机构", "name": "演示账号",
        "email": "demo@example.com", "password": "secret123",
    })
    assert registered.status_code == 200
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite:///./test.db",
        secret_key="test-secret-key-with-at-least-32-bytes",
        demo_login=True,
        demo_login_email="demo@example.com",
    )

    response = client.post(
        "/api/auth/demo-login", json={"email": "111111", "password": "随便输入"}
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["user"]["email"] == "demo@example.com"
    me = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {payload['token']}"},
    )
    assert me.status_code == 200
    assert me.json()["org_name"] == "演示机构"


def test_regular_login_uses_demo_account_when_demo_mode_is_enabled(client):
    registered = client.post("/api/auth/register", json={
        "org_name": "演示机构", "name": "演示账号",
        "email": "demo@example.com", "password": "secret123",
    })
    assert registered.status_code == 200
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite:///./test.db",
        secret_key="test-secret-key-with-at-least-32-bytes",
        demo_login=True,
        demo_login_email="demo@example.com",
    )

    response = client.post(
        "/api/auth/login", json={"email": "111111", "password": "随便输入"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == "demo@example.com"
