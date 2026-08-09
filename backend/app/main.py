import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, get_settings
from .db import Base, engine, get_db
from .routers import auth, projects, reports, scans

logger = logging.getLogger("anjing.api")


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", None),
        }
    }
    if details:
        body["error"]["details"] = details  # type: ignore[index]
    return JSONResponse(status_code=status_code, content=body)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    logger.setLevel(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if app_settings.auto_create_tables:
            Base.metadata.create_all(bind=engine)
        yield

    application = FastAPI(
        title="安龄智境 API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Page-Size"],
        allow_credentials=False,
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.error(
                "request_failed method=%s path=%s status=500 duration_ms=%.2f "
                "request_id=%s exception_type=%s",
                request.method,
                request.url.path,
                elapsed_ms,
                request_id,
                type(exc).__name__,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_complete method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response

    @application.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        codes = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            413: "payload_too_large",
            422: "validation_error",
            503: "service_unavailable",
        }
        message = exc.detail if isinstance(exc.detail, str) else "请求处理失败"
        return _error_response(
            request,
            exc.status_code,
            codes.get(exc.status_code, f"http_{exc.status_code}"),
            message,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"] if part != "body"),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        return _error_response(
            request,
            422,
            "validation_error",
            "请求参数校验失败",
            details,
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error(
            "unhandled_exception path=%s request_id=%s exception_type=%s",
            request.url.path,
            getattr(request.state, "request_id", None),
            type(exc).__name__,
        )
        return _error_response(
            request,
            500,
            "internal_error",
            "服务器内部错误",
        )

    application.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    application.include_router(projects.router, prefix="/api/projects", tags=["projects"])
    application.include_router(scans.router, prefix="/api/scans", tags=["scans"])
    application.include_router(reports.router, prefix="/api/reports", tags=["reports"])
    application.include_router(
        reports.assets_router,
        prefix="/static",
        tags=["report-assets"],
    )

    # 3D 预览渲染器（自研 WebGL）：由后端托管，
    # 避免 Flutter web 的 SPA fallback 劫持 /preview/ 目录请求
    preview_dir = Path(__file__).resolve().parent.parent.parent / "app" / "web" / "preview"
    if preview_dir.is_dir():
        application.mount(
            "/preview",
            StaticFiles(directory=str(preview_dir), html=True),
            name="preview",
        )

    @application.get("/api/health")
    def health():
        return {"ok": True}

    @application.get("/api/health/ready")
    def readiness(db: Session = get_db_dependency()):
        try:
            db.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            logger.warning("database_readiness_failed")
            raise HTTPException(503, "数据库不可用") from exc
        return {"ok": True, "database": "ready"}

    return application


def get_db_dependency():
    from fastapi import Depends

    return Depends(get_db)


app = create_app()
