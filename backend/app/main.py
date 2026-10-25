"""本机项目、资产及分镜API；模型生成按后续迭代接入。"""

import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .api import assets, bootstrap, episodes, jobs, projects, settings, shots
from .config import AppSettings
from .db import Database
from .errors import AppError
from .local_http import is_local_host
from .models import Asset
from .schemas.common import ErrorRead
from .services.storage import StorageService
from .services.tasks import TaskService
from .upload_limit import UploadBodyLimit, is_upload_request

logger = logging.getLogger(__name__)


def error_response(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, **(details or {})}},
        status_code=status,
    )


def create_app(
    config: AppSettings | None = None, *, task_handlers=None, task_policy=None
) -> FastAPI:
    config = config or AppSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(config)
        try:
            database.initialize()
            app.state.database = database
            app.state.storage = StorageService(config.data_dir)
            with database.session_scope() as session:
                registered = set()
                for asset in session.scalars(select(Asset)):
                    registered.add(asset.file_path)
                    if asset.thumbnail_path:
                        registered.add(asset.thumbnail_path)
            app.state.storage.quarantine_orphans(registered)
            app.state.task_service = TaskService(
                database,
                app.state.storage,
                task_handlers,
                task_policy,
            )
            yield
        finally:
            database.close()

    app = FastAPI(
        title="本地视频制作平台",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        responses={
            status: {"model": ErrorRead}
            for status in (403, 404, 409, 413, 415, 422, 429, 500, 503)
        },
    )
    app.state.token = secrets.token_urlsafe(32)
    app.add_middleware(UploadBodyLimit)

    @app.middleware("http")
    async def local_protection(request: Request, call_next):
        hosts = request.headers.getlist("host")
        forwarded = request.headers.getlist("x-forwarded-host")
        # Next转发会改写Host，不能只校验改写后的后端地址。
        if (
            len(hosts) != 1
            or not is_local_host(hosts[0])
            or (forwarded and (len(forwarded) != 1 or not is_local_host(forwarded[0])))
        ):
            return error_response(403, "LOCAL_ONLY", "请通过本机地址访问工作台")
        origin = request.headers.get("origin")
        if origin and origin not in config.frontend_origins:
            return error_response(403, "ORIGIN_REJECTED", "不允许跨站访问本机工作台")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return error_response(403, "ORIGIN_REJECTED", "请直接打开本机工作台")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            supplied = request.headers.get("x-local-token", "")
            if not secrets.compare_digest(supplied, app.state.token):
                return error_response(
                    403, "SESSION_EXPIRED", "页面会话已失效，请刷新后重试"
                )
            upload = is_upload_request(request.method, request.url.path)
            expected = "multipart/form-data" if upload else "application/json"
            if request.headers.get("content-type", "").split(";")[0] != expected:
                return error_response(415, "JSON_REQUIRED", "请使用JSON提交表单")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, error: AppError):
        return error_response(error.status, error.code, error.message, error.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return error_response(
            422, "VALIDATION_ERROR", "输入不合法，请检查字段内容、长度和版本"
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        logger.error("请求未完成，异常类型：%s", type(error).__name__)
        return error_response(500, "INTERNAL_ERROR", "操作未完成，请稍后重试")

    for router in (
        bootstrap.router,
        projects.router,
        episodes.router,
        settings.router,
        assets.router,
        shots.router,
        jobs.router,
    ):
        app.include_router(router, prefix="/api/v1")
    return app
