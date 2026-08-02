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
