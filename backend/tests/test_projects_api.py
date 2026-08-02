def _auth(client):
    client.post("/api/auth/register", json={
        "org_name": "养老院A", "name": "甲", "email": "a1@x.com", "password": "secret123"})
    r = client.post("/api/auth/login", json={"email": "a1@x.com", "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_project_crud_isolated_by_org(client):
    h = _auth(client)
    r = client.post("/api/projects", json={"name": "王奶奶家", "address": "xx路1号"}, headers=h)
    assert r.status_code == 200
    pid = r.json()["id"]
    r = client.get("/api/projects", headers=h)
    assert len(r.json()) == 1 and r.json()[0]["name"] == "王奶奶家"
    # 另一个机构看不到
    client.post("/api/auth/register", json={
        "org_name": "养老院B", "name": "乙", "email": "b1@x.com", "password": "secret123"})
    r2 = client.post("/api/auth/login", json={"email": "b1@x.com", "password": "secret123"})
    h2 = {"Authorization": f"Bearer {r2.json()['token']}"}
    assert client.get("/api/projects", headers=h2).json() == []


def test_get_project_requires_ownership(client):
    h = _auth(client)
    pid = client.post("/api/projects", json={"name": "王奶奶家"}, headers=h).json()["id"]
    client.post("/api/auth/register", json={
        "org_name": "养老院B", "name": "乙", "email": "b1@x.com", "password": "secret123"})
    r2 = client.post("/api/auth/login", json={"email": "b1@x.com", "password": "secret123"})
    h2 = {"Authorization": f"Bearer {r2.json()['token']}"}
    assert client.get(f"/api/projects/{pid}", headers=h2).status_code == 404
