"""football-openclaw-gateway 的阶段 B2 应用。

本进程与 `api/app/main.py` 相互独立：不注册分析接口的路由，不读取它的只读开关。
阶段 B1 的两条只读 GET 仍转到本机 8787 的白名单路径。
阶段 B2 增加 ping 任务的创建与查询，任务数据只写入网关自己的 SQLite 文件。
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import date
from typing import Annotated

from fastapi import APIRouter, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from openclaw_gateway.auth import Unauthorized, ensure_authorized
from openclaw_gateway.config import SERVICE_NAME, STAGE, VERSION, require_auth_on_health
from openclaw_gateway.jobs import (
    CreateTimeout,
    IdempotencyConflict,
    Rejected,
    accept_job,
    fetch_job,
    parse_create_body,
)
from openclaw_gateway.upstream import (
    UpstreamFailure,
    fetch_upstream_json,
    is_safe_match_id,
    match_upstream_url,
    matches_upstream_url,
    shape_match_detail,
)

log = logging.getLogger("openclaw_gateway")

_UNAUTHORIZED_MESSAGE = "缺少 Bearer 令牌，或令牌与服务器配置不一致。"
_NOT_FOUND_MESSAGE = "请求的路径不存在。"
_METHOD_NOT_ALLOWED_MESSAGE = "该路径不支持当前的 HTTP 方法。"
_INTERNAL_ERROR_MESSAGE = "服务内部发生错误，详情已写入日志。"
_MATCH_NOT_FOUND_MESSAGE = "比赛不存在。"
_LIST_NOT_FOUND_MESSAGE = "上游没有找到对应的比赛列表。"
_JOB_NOT_FOUND_MESSAGE = "任务不存在。"
_IDEMPOTENCY_CONFLICT_MESSAGE = "相同的幂等键对应了不同的任务内容。"
_CREATE_TIMEOUT_MESSAGE = "创建任务在限定时间内没有完成。"
_EXTENSION_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_JINGCAI_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LIST_SCOPES = frozenset({"jingcai", "extra", "all"})
# 上游 /matches/dates 是另一条接口。这个路径段不能当成单场编号转发。
_RESERVED_MATCH_SEGMENTS = frozenset({"dates"})
CAPABILITIES: list[dict[str, object]] = [
    {"name": "read.matches", "methods": ["GET /v1/read/matches"]},
    {"name": "read.match", "methods": ["GET /v1/read/matches/{match_id}"]},
    {"name": "jobs.create", "methods": ["POST /v1/jobs"]},
    {"name": "jobs.get", "methods": ["GET /v1/jobs/{id}"]},
]

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
    return error_response(
        501,
        "not_implemented",
        f"该接口已列入规划，但在当前阶段（{STAGE}）尚未实现。",
    )


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
    """声明当前阶段已开放的能力。阶段 B2 在两条读能力之外登记任务创建与查询。"""
    ensure_authorized(authorization, request)
    return {"stage": STAGE, "capabilities": CAPABILITIES}


@router.post("/jobs")
async def create_job(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """创建 ping 任务。鉴权失败不读正文、不落库。成功时返回 202 与任务快照。"""
    ensure_authorized(authorization, request)
    started_at = time.monotonic()
    parsed = parse_create_body(await request.body())
    if isinstance(parsed, Rejected):
        return error_response(parsed.status_code, parsed.code, parsed.message)
    try:
        snapshot = accept_job(parsed, started_at=started_at)
    except IdempotencyConflict:
        return error_response(409, "idempotency_conflict", _IDEMPOTENCY_CONFLICT_MESSAGE)
    except CreateTimeout:
        return error_response(504, "timeout", _CREATE_TIMEOUT_MESSAGE)
    except (sqlite3.Error, OSError) as exc:
        log.error("任务库操作失败。异常类型 %s。", type(exc).__name__)
        return error_response(500, "internal_error", _INTERNAL_ERROR_MESSAGE)
    return JSONResponse(status_code=202, content=snapshot)


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """按编号返回任务快照。任务失败时仍是 200，失败细节在正文 error 字段。"""
    ensure_authorized(authorization, request)
    try:
        snapshot = fetch_job(job_id)
    except (sqlite3.Error, OSError) as exc:
        log.error("任务库操作失败。异常类型 %s。", type(exc).__name__)
        return error_response(500, "internal_error", _INTERNAL_ERROR_MESSAGE)
    if snapshot is None:
        return error_response(404, "not_found", _JOB_NOT_FOUND_MESSAGE)
    return JSONResponse(status_code=200, content=snapshot)


def _valid_jingcai_day(value: str) -> bool:
    """竞彩日必须是真实存在的 YYYY-MM-DD。网关不代填今天。"""
    if _JINGCAI_DAY_RE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _matches_query(request: Request) -> tuple[dict[str, str] | None, JSONResponse | None]:
    """只保留 date 与 scope。其它查询键丢弃，不转发。"""
    dates = request.query_params.getlist("date")
    if len(dates) != 1:
        return None, error_response(
            400,
            "bad_request",
            "查询参数 date 必填，且只能出现一次，格式为 YYYY-MM-DD。",
        )
    if not _valid_jingcai_day(dates[0]):
        return None, error_response(
            400,
            "bad_request",
            "查询参数 date 必须是有效日期，格式为 YYYY-MM-DD。",
        )
    scopes = request.query_params.getlist("scope")
    params = {"date": dates[0]}
    if not scopes:
        return params, None
    if len(scopes) != 1:
        return None, error_response(400, "bad_request", "查询参数 scope 只能出现一次。")
    if scopes[0] not in _LIST_SCOPES:
        return None, error_response(
            400,
            "bad_request",
            "查询参数 scope 只能是 jingcai、extra 或 all。",
        )
    params["scope"] = scopes[0]
    return params, None


def _upstream_error(exc: UpstreamFailure) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


@router.get("/read/matches")
def read_matches(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """按竞彩日转发上游比赛列表。成功时透传上游 JSON。"""
    ensure_authorized(authorization, request)
    params, query_error = _matches_query(request)
    if query_error is not None:
        return query_error
    if params is None:
        return error_response(
            400,
            "bad_request",
            "查询参数 date 必填，且只能出现一次，格式为 YYYY-MM-DD。",
        )
    try:
        payload = fetch_upstream_json(
            matches_upstream_url(),
            params,
            route_name="read.matches",
            not_found_message=_LIST_NOT_FOUND_MESSAGE,
        )
    except UpstreamFailure as exc:
        return _upstream_error(exc)
    return JSONResponse(status_code=200, content=payload)


@router.get("/read/matches/{match_id}")
def read_match(
    match_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """取上游单场详情后精简。不另请求预测接口。"""
    ensure_authorized(authorization, request)
    if match_id in _RESERVED_MATCH_SEGMENTS:
        return not_implemented_response()
    if not is_safe_match_id(match_id):
        return error_response(404, "not_found", _MATCH_NOT_FOUND_MESSAGE)
    try:
        payload = fetch_upstream_json(
            match_upstream_url(match_id),
            None,
            route_name="read.match",
            not_found_message=_MATCH_NOT_FOUND_MESSAGE,
        )
    except UpstreamFailure as exc:
        return _upstream_error(exc)
    if not isinstance(payload, dict):
        return error_response(502, "upstream_error", "上游响应无法解析。")
    return JSONResponse(status_code=200, content=shape_match_detail(match_id, payload))


@router.api_route("/read", methods=_EXTENSION_METHODS)
def read_root(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """读接口前缀本身仍未开放。校验令牌后返回 501，不转发。"""
    ensure_authorized(authorization, request)
    return not_implemented_response()


@router.api_route("/read/{resource_path:path}", methods=_EXTENSION_METHODS)
def read_child(
    resource_path: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """未登记的读子路径。先鉴权，再返回 501，不按子路径转发。"""
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
