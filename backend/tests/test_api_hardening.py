import datetime as dt

import jwt

from app.config import get_settings
from app.db import SessionLocal
from app.models import Report


def _register(client, org: str, email: str):
    response = client.post(
        "/api/auth/register",
        json={
            "org_name": org,
            "name": "测试用户",
            "email": email,
            "password": "secret123",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _auth(client, org: str = "机构A", email: str = "user-a@example.com"):
    result = _register(client, org, email)
    return result, {"Authorization": f"Bearer {result['token']}"}


def test_missing_invalid_expired_and_bad_subject_tokens_return_401(client):
    missing = client.get("/api/auth/me")
    invalid = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    settings = get_settings()
    expired_token = jwt.encode(
        {
            "sub": "1",
            "org": 1,
            "exp": dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    expired = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    bad_subject_token = jwt.encode(
        {
            "sub": "not-an-integer",
            "org": 1,
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    bad_subject = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {bad_subject_token}"},
    )

    for response in (missing, invalid, expired, bad_subject):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


def test_token_for_deleted_user_returns_401(client):
    settings = get_settings()
    token = jwt.encode(
        {
            "sub": "999999",
            "org": 1,
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "用户不存在"


def test_token_organization_mismatch_returns_403(client):
    result = _register(client, "机构A", "org-claim@example.com")
    settings = get_settings()
    token = jwt.encode(
        {
            "sub": str(result["user"]["id"]),
            "org": 999999,
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_cross_organization_resources_are_not_enumerable(client):
    _, owner = _auth(client)
    project = client.post(
        "/api/projects",
        json={"name": "受保护项目"},
        headers=owner,
    ).json()
    scan = client.post(
        f"/api/projects/{project['id']}/scans",
        json={"capture_type": "video"},
        headers=owner,
    ).json()
    _, outsider = _auth(client, "机构B", "user-b@example.com")

    project_response = client.get(
        f"/api/projects/{project['id']}",
        headers=outsider,
    )
    scan_response = client.get(
        f"/api/scans/{scan['id']}",
        headers=outsider,
    )
    create_scan_response = client.post(
        f"/api/projects/{project['id']}/scans",
        json={"capture_type": "video"},
        headers=outsider,
    )
    report_response = client.get(
        f"/api/reports/scans/{scan['id']}",
        headers=outsider,
    )
    scan_list_response = client.get(
        f"/api/projects/{project['id']}/scans",
        headers=outsider,
    )
    upload_response = client.post(
        f"/api/scans/{scan['id']}/upload",
        headers=outsider,
        files={"files": ("not-a-real-video.bin", b"x", "application/octet-stream")},
    )

    for response in (
        project_response,
        scan_response,
        create_scan_response,
        report_response,
        scan_list_response,
        upload_response,
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_project_and_scan_lists_apply_pagination(client):
    _, headers = _auth(client)
    project_ids = []
    for index in range(4):
        project = client.post(
            "/api/projects",
            json={"name": f"项目{index}"},
            headers=headers,
        ).json()
        project_ids.append(project["id"])

    page = client.get("/api/projects?offset=1&limit=2", headers=headers)
    assert page.status_code == 200
    assert [item["id"] for item in page.json()] == list(reversed(project_ids))[1:3]

    project_id = project_ids[0]
    scan_ids = []
    for capture_type in ("video", "photos", "video"):
        scan = client.post(
            f"/api/projects/{project_id}/scans",
            json={"capture_type": capture_type},
            headers=headers,
        ).json()
        scan_ids.append(scan["id"])
    scan_page = client.get(
        f"/api/projects/{project_id}/scans?offset=1&limit=1",
        headers=headers,
    )
    assert [item["id"] for item in scan_page.json()] == list(reversed(scan_ids))[1:2]


def test_pagination_boundaries_use_stable_422_format(client):
    _, headers = _auth(client)
    for query in ("offset=-1", "limit=0", "limit=101"):
        response = client.get(f"/api/projects?{query}", headers=headers)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_input_lengths_whitespace_and_enums_are_validated(client):
    invalid_registrations = [
        {
            "org_name": " ",
            "name": "用户",
            "email": "valid@example.com",
            "password": "secret123",
        },
        {
            "org_name": "机构",
            "name": " ",
            "email": "valid@example.com",
            "password": "secret123",
        },
        {
            "org_name": "机构",
            "name": "用户",
            "email": "not-an-email",
            "password": "secret123",
        },
        {
            "org_name": "机构",
            "name": "用户",
            "email": "valid@example.com",
            "password": "short",
        },
        {
            "org_name": "机构",
            "name": "用户",
            "email": f"{'a' * 64}@{'b' * 48}.example",
            "password": "secret123",
        },
    ]
    for payload in invalid_registrations:
        response = client.post("/api/auth/register", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    _, headers = _auth(client)
    invalid_projects = [
        {"name": " "},
        {"name": "x" * 121},
        {"name": "项目", "address": "x" * 201},
    ]
    for payload in invalid_projects:
        response = client.post("/api/projects", json=payload, headers=headers)
        assert response.status_code == 422

    project_id = client.post(
        "/api/projects",
        json={"name": "合法项目"},
        headers=headers,
    ).json()["id"]
    invalid_scan = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "archive"},
        headers=headers,
    )
    assert invalid_scan.status_code == 422
    assert invalid_scan.json()["error"]["code"] == "validation_error"


def test_email_is_normalized_before_uniqueness_check(client):
    first = _register(client, "机构", "CaseSensitive@Example.com")
    duplicate = client.post(
        "/api/auth/register",
        json={
            "org_name": "机构",
            "name": "另一个用户",
            "email": "casesensitive@example.com",
            "password": "secret123",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={
            "email": "CASESENSITIVE@example.com",
            "password": "secret123",
        },
    )

    assert first["user"]["email"] == "casesensitive@example.com"
    assert duplicate.status_code == 400
    assert login.status_code == 200


def test_report_compare_uses_scan_ids(client):
    _, headers = _auth(client)
    project_id = client.post(
        "/api/projects",
        json={"name": "对比项目"},
        headers=headers,
    ).json()["id"]
    client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "video"},
        headers=headers,
    )
    scan_a = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()
    scan_b = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()

    db = SessionLocal()
    try:
        db.add_all(
            [
                Report(scan_id=scan_a["id"], score=60, risks=[]),
                Report(scan_id=scan_b["id"], score=85, risks=[]),
            ]
        )
        db.commit()
    finally:
        db.close()

    response = client.get(
        f"/api/reports/compare?a={scan_a['id']}&b={scan_b['id']}",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["before"]["scan_id"] == scan_a["id"]
    assert response.json()["after"]["scan_id"] == scan_b["id"]
    assert response.json()["score_delta"] == 25
