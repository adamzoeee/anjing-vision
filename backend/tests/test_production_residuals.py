import logging
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal, _enable_sqlite_foreign_keys, get_db
from app.main import app
from app.models import Organization, Report, Scan, User
from app.tasks import pipeline_tasks
from app.tasks import pipeline_runner


def _register(client, org_name: str, email: str):
    response = client.post(
        "/api/auth/register",
        json={
            "org_name": org_name,
            "name": "测试用户",
            "email": email,
            "password": "secret123",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _auth(client):
    result = _register(client, "测试机构", "owner@example.com")
    return {"Authorization": f"Bearer {result['token']}"}


def test_non_sqlite_connections_are_not_modified():
    class NonSqliteConnection:
        def cursor(self):
            raise AssertionError("non-SQLite connection must not receive PRAGMA")

    _enable_sqlite_foreign_keys(NonSqliteConnection(), None)


def test_organization_and_email_constraints_close_registration_races():
    db = SessionLocal()
    try:
        first_org = Organization(name="唯一机构")
        db.add(first_org)
        db.flush()
        db.add(
            User(
                org_id=first_org.id,
                name="首位用户",
                email="unique@example.com",
                password_hash="hash",
                role="admin",
            )
        )
        db.commit()

        db.add(Organization(name="唯一机构"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        second_org = Organization(name="另一机构")
        db.add(second_org)
        db.flush()
        db.add(
            User(
                org_id=second_org.id,
                name="重复邮箱用户",
                email="unique@example.com",
                password_hash="hash",
                role="member",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        assert db.query(Organization).filter_by(name="唯一机构").count() == 1
        assert db.query(User).filter_by(email="unique@example.com").count() == 1
    finally:
        db.close()


def test_auth_dependency_does_not_turn_database_failures_into_401(
    client,
):
    result = _register(client, "认证机构", "auth@example.com")

    class BrokenSession:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    def broken_database():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_database
    try:
        with pytest.raises(RuntimeError, match="database unavailable"):
            client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {result['token']}"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_dispatch_only_falls_back_for_expected_broker_failures(monkeypatch):
    settings = SimpleNamespace(task_sync=False, allow_sync_fallback=True)
    sync_calls = []

    monkeypatch.setattr(pipeline_tasks, "s", settings)

    def broker_failure(_):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(pipeline_tasks.run_pipeline_async, "delay", broker_failure)
    monkeypatch.setattr(
        pipeline_tasks,
        "run_pipeline_sync",
        lambda scan_id: sync_calls.append(scan_id),
    )

    pipeline_tasks.dispatch_scan(12)

    assert sync_calls == [12]


def test_dispatch_does_not_swallow_programming_errors(monkeypatch):
    monkeypatch.setattr(
        pipeline_tasks,
        "s",
        SimpleNamespace(task_sync=False, allow_sync_fallback=False),
    )

    def programming_error(_):
        raise TypeError("programming error")

    monkeypatch.setattr(
        pipeline_tasks.run_pipeline_async,
        "delay",
        programming_error,
    )

    with pytest.raises(TypeError, match="programming error"):
        pipeline_tasks.dispatch_scan(12)


def test_pipeline_failure_boundary_sanitizes_status_and_logs(
    client,
    monkeypatch,
    caplog,
):
    headers = _auth(client)
    project_id = client.post(
        "/api/projects",
        json={"name": "管道异常"},
        headers=headers,
    ).json()["id"]
    scan_id = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()["id"]

    def fail_media_path(_):
        raise RuntimeError("password=hidden C:\\private\\media")

    monkeypatch.setattr(pipeline_runner, "media_path", fail_media_path)
    caplog.set_level(logging.ERROR, logger="anjing.pipeline")

    pipeline_runner.run_pipeline(scan_id)

    db = SessionLocal()
    try:
        scan = db.get(Scan, scan_id)
        assert scan.status == "failed"
        assert scan.message == "管道处理失败，请稍后重试"
    finally:
        db.close()
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "exception_type=RuntimeError" in messages
    assert "password=hidden" not in messages
    assert "C:\\private" not in messages


def test_report_compare_has_clear_parameters_and_keeps_flutter_compatibility(client):
    headers = _auth(client)
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
    before_scan = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()
    after_scan = client.post(
        f"/api/projects/{project_id}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()
    other_project_id = client.post(
        "/api/projects",
        json={"name": "其他项目"},
        headers=headers,
    ).json()["id"]
    other_scan = client.post(
        f"/api/projects/{other_project_id}/scans",
        json={"capture_type": "video"},
        headers=headers,
    ).json()

    db = SessionLocal()
    try:
        db.add_all(
            [
                Report(scan_id=before_scan["id"], score=60, risks=[]),
                Report(scan_id=after_scan["id"], score=85, risks=[]),
                Report(scan_id=other_scan["id"], score=70, risks=[]),
            ]
        )
        db.commit()
    finally:
        db.close()

    canonical_response = client.get(
        "/api/reports/compare",
        params={
            "before_scan_id": before_scan["id"],
            "after_scan_id": after_scan["id"],
        },
        headers=headers,
    )
    flutter_compatible_response = client.get(
        f"/api/reports/compare?a={before_scan['id']}&b={after_scan['id']}",
        headers=headers,
    )
    report_id_response = client.get(
        "/api/reports/compare?a=1&b=2",
        headers=headers,
    )
    different_project_response = client.get(
        "/api/reports/compare",
        params={
            "before_scan_id": before_scan["id"],
            "after_scan_id": other_scan["id"],
        },
        headers=headers,
    )
    conflicting_response = client.get(
        "/api/reports/compare",
        params={
            "before_scan_id": before_scan["id"],
            "after_scan_id": after_scan["id"],
            "a": other_scan["id"],
            "b": after_scan["id"],
        },
        headers=headers,
    )
    documented_parameters = {
        parameter["name"]
        for parameter in app.openapi()["paths"]["/api/reports/compare"]["get"]["parameters"]
    }

    assert canonical_response.status_code == 200
    assert canonical_response.json()["before"]["scan_id"] == before_scan["id"]
    assert canonical_response.json()["after"]["scan_id"] == after_scan["id"]
    assert canonical_response.json()["score_delta"] == 25
    assert flutter_compatible_response.json() == canonical_response.json()
    assert report_id_response.status_code == 404
    assert different_project_response.status_code == 404
    assert conflicting_response.status_code == 422
    assert documented_parameters == {"before_scan_id", "after_scan_id"}
