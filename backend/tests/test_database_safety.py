import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.main import app


class _TrackingSession:
    def __init__(self):
        self.rolled_back = False
        self.closed = False

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_database_dependency_rolls_back_and_closes_on_exception(monkeypatch):
    session = _TrackingSession()
    monkeypatch.setattr("app.db.SessionLocal", lambda: session)
    dependency = get_db()

    assert next(dependency) is session
    with pytest.raises(RuntimeError, match="request failed"):
        dependency.throw(RuntimeError("request failed"))

    assert session.rolled_back is True
    assert session.closed is True


def test_database_constraint_error_is_rolled_back_and_sanitized(
    client,
    monkeypatch,
):
    rollback_calls = 0
    original_rollback = Session.rollback

    def fail_commit(_):
        raise IntegrityError(
            "INSERT INTO users ... password_hash=secret",
            {},
            RuntimeError("duplicate key"),
        )

    def track_rollback(session):
        nonlocal rollback_calls
        rollback_calls += 1
        return original_rollback(session)

    monkeypatch.setattr(Session, "commit", fail_commit)
    monkeypatch.setattr(Session, "rollback", track_rollback)

    response = client.post(
        "/api/auth/register",
        json={
            "org_name": "并发注册机构",
            "name": "用户",
            "email": "race@example.com",
            "password": "secret123",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "邮箱或机构已存在"
    assert "INSERT INTO" not in response.text
    assert "password_hash" not in response.text
    assert rollback_calls >= 1


def test_initial_migration_builds_schema_on_empty_database(tmp_path, monkeypatch):
    database = tmp_path / "migration.db"
    previous_url = os.environ.get("DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    get_settings.cache_clear()
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "head")
    finally:
        if previous_url is None:
            monkeypatch.delenv("DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("DATABASE_URL", previous_url)
        get_settings.cache_clear()

    schema = inspect(create_engine(f"sqlite:///{database.as_posix()}"))
    assert {
        "alembic_version",
        "organizations",
        "users",
        "projects",
        "scans",
        "reports",
    }.issubset(schema.get_table_names())
    report_constraints = schema.get_unique_constraints("reports")
    assert any(item["column_names"] == ["scan_id"] for item in report_constraints)


def test_database_readiness_failure_returns_503(client):
    class BrokenSession:
        def execute(self, _):
            raise RuntimeError("database connection password=hidden")

    def broken_database():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_database
    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "http_503"
    assert response.json()["error"]["message"] == "数据库不可用"
    assert "password=hidden" not in response.text
