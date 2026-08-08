import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import EnvironmentMode, Settings
from app.main import create_app


def _production_settings(**overrides) -> Settings:
    values = {
        "environment": EnvironmentMode.production,
        "secret_key": "production-secret-key-with-at-least-32-bytes",
        "cors_origins": ["https://app.example.com"],
        "auto_create_tables": False,
        "allow_sync_fallback": False,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    "secret",
    ["change-me", "change-me-in-production", "short-secret"],
)
def test_production_rejects_weak_secret(secret):
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        _production_settings(secret_key=secret)


def test_production_requires_explicit_cors_allowlist():
    with pytest.raises(ValidationError, match="http\\(s\\) origin"):
        _production_settings(cors_origins=["*"])
    with pytest.raises(ValidationError, match="本地开发地址"):
        _production_settings(cors_origins=["http://localhost:3000"])
    with pytest.raises(ValidationError, match="http\\(s\\) origin"):
        _production_settings(cors_origins=["https://*.example.com"])


@pytest.mark.parametrize(
    "origin",
    [
        "not-an-origin",
        "ftp://app.example.com",
        "https://user:password@app.example.com",
        "https://app.example.com/path",
        "https://app.example.com?token=secret",
    ],
)
def test_cors_origins_must_be_valid_origins(origin):
    with pytest.raises(ValidationError, match="http\\(s\\) origin"):
        Settings(cors_origins=[origin])


def test_database_url_must_be_parseable():
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(database_url="://invalid")


def test_production_minio_rejects_default_credentials():
    with pytest.raises(ValidationError, match="MinIO"):
        _production_settings(storage_backend="minio")


def test_development_cors_allows_local_origin():
    app = create_app(
        Settings(
            environment=EnvironmentMode.development,
            auto_create_tables=False,
        )
    )
    with TestClient(app) as client:
        response = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_production_cors_only_allows_configured_origin():
    app = create_app(_production_settings())
    with TestClient(app) as client:
        allowed = client.options(
            "/api/health",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        rejected = client.options(
            "/api/health",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert allowed.headers["access-control-allow-origin"] == "https://app.example.com"
    assert "access-control-allow-origin" not in rejected.headers


def test_error_responses_are_stable_and_sanitized():
    app = create_app(
        Settings(
            environment=EnvironmentMode.test,
            auto_create_tables=False,
        )
    )

    @app.get("/test/unhandled")
    def unhandled():
        raise RuntimeError("database password=do-not-expose at C:\\private\\db.py")

    with TestClient(app, raise_server_exceptions=False) as client:
        not_found = client.get("/missing")
        invalid = client.post(
            "/api/auth/login",
            json={"email": "not-an-email", "password": "secret-password"},
        )
        internal = client.get("/test/unhandled")

    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "not_found"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert "secret-password" not in invalid.text
    assert internal.status_code == 500
    assert internal.json()["error"]["message"] == "服务器内部错误"
    assert "do-not-expose" not in internal.text
    assert "C:\\private" not in internal.text


def test_request_log_does_not_include_password_or_authorization(client, caplog):
    caplog.set_level(logging.INFO, logger="anjing.api")
    password = "password-that-must-not-be-logged"
    token = "authorization-token-that-must-not-be-logged"

    client.post(
        "/api/auth/login",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": "nobody@example.com", "password": password},
    )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "method=POST" in messages
    assert "path=/api/auth/login" in messages
    assert "request_id=" in messages
    assert password not in messages
    assert token not in messages


def test_liveness_and_database_readiness(client):
    live = client.get("/api/health")
    ready = client.get("/api/health/ready")

    assert live.status_code == 200
    assert live.json() == {"ok": True}
    assert ready.status_code == 200
    assert ready.json() == {"ok": True, "database": "ready"}
