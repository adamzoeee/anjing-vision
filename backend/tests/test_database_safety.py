import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import create_engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.main import app
from app.models import Organization, Scan, User


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


def test_registration_flush_constraint_error_is_rolled_back_and_sanitized(
    client,
    monkeypatch,
):
    rollback_calls = 0
    original_flush = Session.flush
    original_rollback = Session.rollback

    def fail_organization_flush(session, *args, **kwargs):
        if any(isinstance(item, Organization) for item in session.new):
            raise IntegrityError(
                "INSERT INTO organizations ... secret-column",
                {},
                RuntimeError("duplicate organization"),
            )
        return original_flush(session, *args, **kwargs)

    def track_rollback(session):
        nonlocal rollback_calls
        rollback_calls += 1
        return original_rollback(session)

    monkeypatch.setattr(Session, "flush", fail_organization_flush)
    monkeypatch.setattr(Session, "rollback", track_rollback)

    response = client.post(
        "/api/auth/register",
        json={
            "org_name": "并发机构",
            "name": "用户",
            "email": "flush-race@example.com",
            "password": "secret123",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "邮箱或机构已存在"
    assert "INSERT INTO" not in response.text
    assert "secret-column" not in response.text
    assert rollback_calls >= 1


def test_email_unique_constraint_rejects_racing_insert_and_session_recovers():
    db = SessionLocal()
    try:
        organization = Organization(name="唯一约束机构")
        db.add(organization)
        db.flush()
        db.add(
            User(
                org_id=organization.id,
                name="用户一",
                email="database-unique@example.com",
                password_hash="hash",
                role="admin",
            )
        )
        db.commit()

        db.add(
            User(
                org_id=organization.id,
                name="用户二",
                email="database-unique@example.com",
                password_hash="hash",
                role="member",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        assert (
            db.query(User)
            .filter(User.email == "database-unique@example.com")
            .count()
            == 1
        )
    finally:
        db.close()


def test_sqlite_foreign_keys_are_enabled_and_reject_orphans():
    db = SessionLocal()
    try:
        assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        db.add(Scan(project_id=999999, capture_type="video"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_database_allows_only_one_admin_per_organization():
    db = SessionLocal()
    try:
        organization = Organization(name="管理员约束机构")
        db.add(organization)
        db.flush()
        db.add(
            User(
                org_id=organization.id,
                name="管理员一",
                email="admin-one@example.com",
                password_hash="hash",
                role="admin",
            )
        )
        db.commit()
        db.add(
            User(
                org_id=organization.id,
                name="管理员二",
                email="admin-two@example.com",
                password_hash="hash",
                role="admin",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_existing_organization_registration_cannot_race_for_admin(client):
    db = SessionLocal()
    try:
        db.add(Organization(name="预创建机构"))
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/auth/register",
        json={
            "org_name": "预创建机构",
            "name": "普通成员",
            "email": "member@example.com",
            "password": "secret123",
        },
    )

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "member"


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
    user_indexes = schema.get_indexes("users")
    assert any(
        item["name"] == "uq_users_single_admin_per_org" and item["unique"]
        for item in user_indexes
    )


def test_database_readiness_failure_returns_503(client):
    class BrokenSession:
        def execute(self, _):
            raise OperationalError(
                "SELECT 1 password=hidden",
                {},
                RuntimeError("connection failed"),
            )

    def broken_database():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_database
    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert response.json()["error"]["message"] == "数据库不可用"
    assert "password=hidden" not in response.text
