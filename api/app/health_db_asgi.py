"""GET /health/db 的访问边界。未授权或可写实例在打开数据库之前拒绝。

令牌只从 FAT_HEALTH_DB_TOKEN_FILE 指向的文件读取。文件内容不写进仓库。
"""
from __future__ import annotations

import hmac
import json
import os
import sqlite3
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response

from app.running_db import read_identity

_TOKEN_HEADER = b"x-fat-health-db-token"
_DENIED = b'{"ok":false}'
_WRITABLE = b'{"ok":false,"read_only":false}'


def _expected_token() -> str | None:
    configured = os.environ.get("FAT_HEALTH_DB_TOKEN_FILE", "").strip()
    if not configured:
        return None
    try:
        text = Path(configured).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _provided_token(headers: list[tuple[bytes, bytes]]) -> str:
    for key, value in headers:
        if key.lower() == _TOKEN_HEADER:
            return value.decode("latin-1")
    return ""


def _same_token(provided: str, expected: str) -> bool:
    try:
        return hmac.compare_digest(provided, expected)
    except (TypeError, ValueError):
        return False


def invoke_health_db(headers: list[tuple[bytes, bytes]]) -> tuple[int, bytes]:
    """先核对内部令牌和只读实例，通过后才连接数据库。"""
    expected = _expected_token()
    provided = _provided_token(headers)
    if expected is None or not provided or not _same_token(provided, expected):
        return 401, _DENIED
    import app.db as database

    if not database.READONLY:
        return 403, _WRITABLE
    if not Path(database.DB_PATH).is_file():
        return 403, _WRITABLE
    try:
        conn = database.connect()
    except sqlite3.Error:
        return 403, _WRITABLE
    try:
        identity = read_identity(conn)
    finally:
        conn.close()
    if identity.get("read_only") is not True or identity.get("ok") is not True:
        return 403, _WRITABLE
    return 200, json.dumps(identity, ensure_ascii=False).encode("utf-8")


async def health_db_asgi(scope, receive, send) -> None:
    """供 ASGI 服务器直接挂载的只读身份入口。"""
    if scope.get("type") != "http":
        return
    while True:
        message = await receive()
        if not message.get("more_body"):
            break
    if scope.get("method") != "GET":
        status, body = 405, _DENIED
    else:
        status, body = invoke_health_db(list(scope.get("headers") or []))
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def register_health_db(app) -> None:
    """登记与生产进程相同的 FastAPI 路由。路由函数本身不连接数据库。"""

    @app.get("/health/db")
    async def health_db(request: Request):
        status, body = invoke_health_db(list(request.scope["headers"]))
        return Response(content=body, status_code=status, media_type="application/json")
