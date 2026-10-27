"""football-openclaw-gateway 的阶段 B0 应用。

本进程与 `api/app/main.py` 相互独立：不注册分析接口的路由，不读取它的只读开关，
也不把请求转发到 8787。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from openclaw_gateway.auth import Unauthorized, ensure_authorized
from openclaw_gateway.config import SERVICE_NAME, STAGE, VERSION, require_auth_on_health

log = logging.getLogger("openclaw_gateway")

_UNAUTHORIZED_MESSAGE = "缺少 Bearer 令牌，或令牌与服务器配置不一致。"
_NOT_IMPLEMENTED_MESSAGE = "该接口已列入规划，但在当前阶段（b0）尚未实现。"
_NOT_FOUND_MESSAGE = "请求的路径不存在。"
_METHOD_NOT_ALLOWED_MESSAGE = "该路径不支持当前的 HTTP 方法。"
_INTERNAL_ERROR_MESSAGE = "服务内部发生错误，详情已写入日志。"
_EXTENSION_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

router = APIRouter(prefix="/v1")


def error_response(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """渲染统一错误正文。

    500 由最外层服务器错误中间件直接发出，不会经过内层的缓存头包装，因此这里自己带上 no-store。
    """
    response_headers = {"Cache-Control": "no-store"}
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=response_headers,
    )


def unauthorized_response() -> JSONResponse:
    """渲染 401。不回显调用方提交的令牌。"""
    return error_response(
        401,
        "unauthorized",
        _UNAUTHORIZED_MESSAGE,
        headers={"WWW-Authenticate": 'Bearer realm="football-openclaw-gateway"'},
    )


def not_implemented_response() -> JSONResponse:
    """渲染已规划但尚未实现的扩展点。"""
    return error_response(501, "not_implemented", _NOT_IMPLEMENTED_MESSAGE)


def handle_unauthorized(_request: Request, _exc: Unauthorized) -> JSONResponse:
    """把鉴权失败渲染为合同约定的 401 正文。"""
    return unauthorized_response()


def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """把框架产生的 404 与 405 收成统一错误正文，不回显框架原文。"""
    if exc.status_code == 404:
        return error_response(404, "not_found", _NOT_FOUND_MESSAGE)
    if exc.status_code == 405:
        return error_response(405, "method_not_allowed", _METHOD_NOT_ALLOWED_MESSAGE)
    if exc.status_code == 401:
        return unauthorized_response()
    if exc.status_code >= 500:
        return error_response(exc.status_code, "internal_error", _INTERNAL_ERROR_MESSAGE)
    return error_response(exc.status_code, "internal_error", _INTERNAL_ERROR_MESSAGE)


def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """记录未预期异常的类型与路径，响应正文不包含异常信息。"""
    if isinstance(exc, Unauthorized):
        return unauthorized_response()
    if isinstance(exc, StarletteHTTPException):
        return handle_http_exception(request, exc)
    log.error(
        "未预期的异常。方法 %s，路径 %s，异常类型 %s。",
        request.method,
        request.url.path,
        type(exc).__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return error_response(500, "internal_error", _INTERNAL_ERROR_MESSAGE)


def _health_body() -> dict[str, object]:
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "version": VERSION,
        "stage": STAGE,
    }


@router.get("/health")
def health(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """进程探活。默认不校验令牌；环境变量要求时与其它接口使用同一套校验。"""
    if require_auth_on_health():
        ensure_authorized(authorization, request)
    return _health_body()


@router.get("/capabilities")
def capabilities(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """声明当前阶段已开放的能力。阶段 B0 固定返回空列表。"""
    ensure_authorized(authorization, request)
    return {"stage": STAGE, "capabilities": []}


@router.post("/jobs")
def create_job(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """创建任务的预留入口。阶段 B0 校验令牌后固定返回 501，不读取、不保存请求体。"""
    ensure_authorized(authorization, request)
    return not_implemented_response()


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """查询任务的预留入口。阶段 B0 不区分任务是否存在，校验令牌后固定返回 501。"""
    del job_id
    ensure_authorized(authorization, request)
    return not_implemented_response()


@router.api_route("/read", methods=_EXTENSION_METHODS)
def read_root(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """读接口前缀的预留入口。阶段 B0 不转发到现有分析接口。"""
    ensure_authorized(authorization, request)
    return not_implemented_response()


@router.api_route("/read/{resource_path:path}", methods=_EXTENSION_METHODS)
def read_child(
    resource_path: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """读接口子路径的预留入口。阶段 B0 不按子路径转发。"""
    del resource_path
    ensure_authorized(authorization, request)
    return not_implemented_response()


class NoStoreMiddleware:
    """为每条 HTTP 响应加上 Cache-Control: no-store。

    使用纯 ASGI 包装发送过程。不使用 BaseHTTPMiddleware，以免已转成响应的异常被再次抛出。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_no_store(message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_no_store)


def create_app() -> FastAPI:
    """构造应用。关闭交互式文档，避免出现合同以外的未鉴权页面。"""
    app = FastAPI(
        title=SERVICE_NAME,
        version=VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.add_middleware(NoStoreMiddleware)
    app.add_exception_handler(Unauthorized, handle_unauthorized)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected)
    app.include_router(router)
    return app


app = create_app()
