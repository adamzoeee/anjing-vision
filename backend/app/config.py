from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# 把 .env 全部变量注入 os.environ（override=False：显式环境变量优先），
# 使 VID2SCENE_* / GAUSSIAN_* 这类非 Settings 字段也能通过 .env 配置，
# 供 pipeline 代码用 os.getenv 读取。
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


class EnvironmentMode(str, Enum):
    development = "development"
    test = "test"
    production = "production"


_DEVELOPMENT_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8080",
]
_WEAK_SECRETS = {
    "change-me",
    "change-me-in-production",
    "dev-secret-change-me-please-use-env-32bytes",
    "secret",
    "test-secret",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: EnvironmentMode = EnvironmentMode.development
    database_url: str = "sqlite:///./anjing.db"
    secret_key: str = "dev-secret-change-me-please-use-env-32bytes"
    token_expire_minutes: int = Field(default=60 * 24 * 7, ge=5, le=60 * 24 * 30)
    cors_origins: list[str] = Field(default_factory=lambda: list(_DEVELOPMENT_ORIGINS))

    storage_backend: Literal["local", "minio"] = "local"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "anjing"
    minio_secure: bool = False
    data_dir: str = "./data"

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    task_sync: bool = False
    allow_sync_fallback: bool = True

    apriltag_enabled: bool = True
    apriltag_family: str = "tagStandard41h12"
    apriltag_size_m: float = Field(default=0.09, gt=0.0, le=1.0)

    auto_create_tables: bool = True
    default_page_size: int = Field(default=20, ge=1, le=100)
    max_page_size: int = Field(default=100, ge=1, le=500)
    max_upload_bytes: int = Field(default=512 * 1024 * 1024, ge=1024)
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator(
        "database_url",
        "secret_key",
        "minio_endpoint",
        "minio_access_key",
        "minio_secret_key",
        "minio_bucket",
        "data_dir",
        "redis_url",
        "celery_broker_url",
        "host",
        mode="before",
    )
    @classmethod
    def _strip_non_empty_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("配置值不能为空")
        return value

    @field_validator("cors_origins")
    @classmethod
    def _normalize_origins(cls, origins: list[str]) -> list[str]:
        normalized = []
        for origin in origins:
            value = origin.strip().rstrip("/")
            parsed = urlsplit(value)
            try:
                parsed_port = parsed.port
            except ValueError as exc:
                raise ValueError(f"CORS 来源端口无效: {value}") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or "*" in value
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed_port is not None
                and not 1 <= parsed_port <= 65535
            ):
                raise ValueError(f"CORS 来源必须是有效的 http(s) origin: {value}")
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        try:
            parsed = make_url(value)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL 格式无效") from exc
        if not parsed.drivername:
            raise ValueError("DATABASE_URL 必须包含数据库驱动")
        return value

    @model_validator(mode="after")
    def _validate_environment_safety(self) -> "Settings":
        if self.max_page_size < self.default_page_size:
            raise ValueError("MAX_PAGE_SIZE 不能小于 DEFAULT_PAGE_SIZE")
        if self.apriltag_enabled and self.apriltag_family != "tagStandard41h12":
            raise ValueError("当前米制标定仅支持 tagStandard41h12")

        if self.environment == EnvironmentMode.production:
            if len(self.secret_key.encode("utf-8")) < 32 or self.secret_key.lower() in _WEAK_SECRETS:
                raise ValueError("生产环境 SECRET_KEY 必须是至少 32 字节的随机密钥")
            if not self.cors_origins:
                raise ValueError("生产环境必须配置 CORS_ORIGINS 白名单")
            if "*" in self.cors_origins:
                raise ValueError("生产环境 CORS_ORIGINS 不允许使用通配符")
            local_hosts = {"localhost", "127.0.0.1", "::1"}
            if any(
                (urlsplit(item).hostname or "").lower() in local_hosts
                for item in self.cors_origins
            ):
                raise ValueError("生产环境 CORS_ORIGINS 不允许使用本地开发地址")
            if self.auto_create_tables:
                raise ValueError("生产环境必须关闭 AUTO_CREATE_TABLES 并使用数据库迁移")
            if self.allow_sync_fallback:
                raise ValueError("生产环境必须关闭 ALLOW_SYNC_FALLBACK")
            if self.storage_backend == "minio" and (
                self.minio_access_key == "minioadmin" or self.minio_secret_key == "minioadmin"
            ):
                raise ValueError("生产环境 MinIO 凭据不能使用默认值")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
