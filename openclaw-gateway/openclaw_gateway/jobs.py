"""阶段 B2 的最小任务存储与 ping 执行。

只接受白名单类型 ping。任务写入独立的 SQLite 文件，不写入比赛业务库，
也不访问本机 8787。极短任务在创建请求内跑完，对外仍返回任务快照。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openclaw_gateway.config import (
    job_create_timeout_seconds,
    job_db_path,
    job_max_seconds,
    job_retention_seconds,
)

log = logging.getLogger("openclaw_gateway")

_BEIJING = timezone(timedelta(hours=8))
_MAX_BODY_BYTES = 65_536
_MAX_ECHO_CHARS = 128
_MAX_IDEMPOTENCY_CHARS = 128
_MAX_CLIENT_REF_CHARS = 256
_ALLOWED_BODY_KEYS = frozenset({"type", "payload", "idempotency_key", "client_ref"})
_PING_PAYLOAD_KEYS = frozenset({"echo"})
_TERMINAL_STATUSES = frozenset({"succeeded", "failed"})
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT,
    error_json TEXT,
    client_ref TEXT,
    idempotency_key TEXT,
    canonical_json TEXT NOT NULL,
    entered_running INTEGER NOT NULL DEFAULT 0,
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency_key
    ON jobs(idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_finished_at ON jobs(finished_at);
"""

_lock = threading.Lock()
_connections: dict[str, sqlite3.Connection] = {}


class IdempotencyConflict(Exception):
    """同一幂等键对应了不同的规范化正文。"""


class CreateTimeout(Exception):
    """创建请求在处理时限内没有返回。任务行可能已经进入终态。"""


@dataclass(frozen=True)
class Rejected:
    """校验失败。调用方把它渲染成统一错误正文。"""

    status_code: int
    code: str
    message: str


@dataclass(frozen=True)
class CreateRequest:
    """通过校验、尚未落库的创建请求。"""

    job_type: str
    payload: dict[str, Any]
    canonical_json: str
    idempotency_key: str | None
    client_ref: str | None


def now_iso() -> str:
    """当前时刻，固定为北京时间偏移，精度到秒。"""
    return datetime.now(_BEIJING).isoformat(timespec="seconds")


def parse_create_body(raw: bytes) -> CreateRequest | Rejected:
    """解析创建请求。未知字段拒绝，不把字段值写进错误说明。"""
    if len(raw) > _MAX_BODY_BYTES:
        return Rejected(400, "bad_request", "请求体过大。")
    if not raw:
        return Rejected(400, "bad_request", "请求体必须是 JSON 对象。")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Rejected(400, "bad_request", "请求体必须是 JSON 对象。")
    if not isinstance(body, dict):
        return Rejected(400, "bad_request", "请求体必须是 JSON 对象。")
    if not set(body).issubset(_ALLOWED_BODY_KEYS):
        return Rejected(400, "bad_request", "请求体含有不支持的字段。")
    if "type" not in body or not isinstance(body["type"], str) or body["type"] != "ping":
        return Rejected(400, "bad_request", "不支持的任务类型。")
    payload = body.get("payload", _MISSING)
    if not isinstance(payload, dict):
        return Rejected(400, "bad_request", "payload 必须是对象。")
    if not set(payload).issubset(_PING_PAYLOAD_KEYS):
        return Rejected(400, "bad_request", "payload 含有不支持的字段。")
    if "echo" in payload and not _valid_echo(payload["echo"]):
        return Rejected(400, "bad_request", "payload.echo 必须是不超过 128 个字符的字符串。")
    idempotency_key, idem_error = _optional_idempotency_key(body)
    if idem_error is not None:
        return idem_error
    client_ref, ref_error = _optional_client_ref(body)
    if ref_error is not None:
        return ref_error
    canonical = json.dumps(
        {"type": "ping", "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return CreateRequest(
        job_type="ping",
        payload=payload,
        canonical_json=canonical,
        idempotency_key=idempotency_key,
        client_ref=client_ref,
    )


def accept_job(spec: CreateRequest, *, started_at: float) -> dict[str, Any]:
    """写入任务并同步执行 ping。重复的幂等键返回已有快照。

    存储会先记入 running，再进入终态。创建响应只带终态快照。
    超过创建时限时抛出 CreateTimeout；任务若已跑完，仍留在库里。
    """
    with _lock:
        timeout_seconds = job_create_timeout_seconds()
        max_seconds = job_max_seconds()
        _purge_expired_locked()
        if spec.idempotency_key is not None:
            existing = _find_by_idempotency_key_locked(spec.idempotency_key)
            if existing is not None:
                if existing["canonical_json"] != spec.canonical_json:
                    raise IdempotencyConflict()
                return _snapshot(existing)
        if _deadline_exceeded(started_at, timeout_seconds):
            raise CreateTimeout()
        job_id = _new_job_id()
        created_at = now_iso()
        _insert_queued_locked(spec, job_id, created_at)
        _mark_running_locked(job_id, now_iso())
        exec_started = time.monotonic()
        try:
            execute_whitelisted(spec.job_type, spec.payload)
        except Exception as exc:
            log.error(
                "任务执行失败。任务 %s，异常类型 %s。",
                job_id,
                type(exc).__name__,
            )
            _finish_locked(
                job_id,
                status="failed",
                result=None,
                error={"code": "job_internal_error", "message": "任务执行失败。"},
                stamp=now_iso(),
            )
        else:
            if time.monotonic() - exec_started > max_seconds:
                _finish_locked(
                    job_id,
                    status="failed",
                    result=None,
                    error={"code": "job_timeout", "message": "任务执行超过限定时间。"},
                    stamp=now_iso(),
                )
            else:
                stamp = now_iso()
                _finish_locked(
                    job_id,
                    status="succeeded",
                    result=_ping_result(spec.payload, stamp),
                    error=None,
                    stamp=stamp,
                )
        row = _find_by_id_locked(job_id)
        if row is None:
            raise sqlite3.OperationalError("任务写入后无法读回")
        if _deadline_exceeded(started_at, timeout_seconds):
            raise CreateTimeout()
        return _snapshot(row)


def fetch_job(job_id: str) -> dict[str, Any] | None:
    """按编号取当前快照。已过保留期的终态任务视为不存在。"""
    if not isinstance(job_id, str) or job_id == "" or len(job_id) > 80:
        return None
    with _lock:
        _purge_expired_locked()
        row = _find_by_id_locked(job_id)
        if row is None:
            return None
        return _snapshot(row)


def execute_whitelisted(job_type: str, payload: dict[str, Any]) -> None:
    """执行白名单任务。ping 不访问上游、不写业务库。

    结果由调用方按类型组装。本函数留作执行计时的边界，测试可以替换它。
    """
    if job_type != "ping":
        raise RuntimeError("未白名单的任务类型不应进入执行")
    del payload


def close_connections() -> None:
    """关闭本进程缓存的任务库连接。单测在换临时文件后调用。"""
    with _lock:
        for conn in _connections.values():
            conn.close()
        _connections.clear()


def _ping_result(payload: dict[str, Any], server_time: str) -> dict[str, Any]:
    echo = payload["echo"] if "echo" in payload else None
    return {"ok": True, "echo": echo, "server_time": server_time}


def _snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "result": _loads_object(row["result_json"]),
        "error": _loads_object(row["error_json"]),
        "client_ref": row["client_ref"],
        "idempotency_key": row["idempotency_key"],
    }


def _loads_object(raw: str | None) -> Any:
    if raw is None:
        return None
    return json.loads(raw)


def _deadline_exceeded(started_at: float, timeout_seconds: float) -> bool:
    return time.monotonic() - started_at >= timeout_seconds


def _new_job_id() -> str:
    """生成不可按连续整数猜测的任务编号。"""
    return "job_" + secrets.token_hex(16)


def _connection_locked() -> sqlite3.Connection:
    path = job_db_path()
    conn = _connections.get(path)
    if conn is not None:
        return conn
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _tighten_file_mode(path)
    _connections[path] = conn
    return conn


def _tighten_file_mode(path: str) -> None:
    """任务库可能含调用方引用号，文件权限收成仅属主可读写。"""
    for suffix in ("", "-wal", "-shm"):
        candidate = path + suffix
        if not os.path.exists(candidate):
            continue
        try:
            os.chmod(candidate, 0o600)
        except OSError:
            log.warning("任务库文件权限未能收紧。")


def _purge_expired_locked() -> None:
    cutoff = (
        datetime.now(_BEIJING) - timedelta(seconds=job_retention_seconds())
    ).isoformat(timespec="seconds")
    conn = _connection_locked()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            DELETE FROM jobs
            WHERE status IN ('succeeded', 'failed')
              AND finished_at IS NOT NULL
              AND finished_at <= ?
            """,
            (cutoff,),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _find_by_idempotency_key_locked(key: str) -> sqlite3.Row | None:
    conn = _connection_locked()
    return conn.execute(
        "SELECT * FROM jobs WHERE idempotency_key = ?",
        (key,),
    ).fetchone()


def _find_by_id_locked(job_id: str) -> sqlite3.Row | None:
    conn = _connection_locked()
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def _insert_queued_locked(spec: CreateRequest, job_id: str, created_at: str) -> None:
    conn = _connection_locked()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO jobs (
                id, type, status, created_at, updated_at, finished_at,
                result_json, error_json, client_ref, idempotency_key,
                canonical_json, entered_running
            ) VALUES (?, ?, 'queued', ?, ?, NULL, NULL, NULL, ?, ?, ?, 0)
            """,
            (
                job_id,
                spec.job_type,
                created_at,
                created_at,
                spec.client_ref,
                spec.idempotency_key,
                spec.canonical_json,
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _mark_running_locked(job_id: str, stamp: str) -> None:
    conn = _connection_locked()
    conn.execute("BEGIN IMMEDIATE")
    try:
        updated = conn.execute(
            """
            UPDATE jobs
            SET status = 'running', updated_at = ?, entered_running = 1
            WHERE id = ? AND status = 'queued'
            """,
            (stamp, job_id),
        )
        if updated.rowcount != 1:
            raise sqlite3.OperationalError("任务未能进入 running")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _finish_locked(
    job_id: str,
    *,
    status: str,
    result: dict[str, Any] | None,
    error: dict[str, str] | None,
    stamp: str,
) -> None:
    if status not in _TERMINAL_STATUSES:
        raise RuntimeError("终态只允许 succeeded 或 failed")
    if status == "succeeded" and (result is None or error is not None):
        raise RuntimeError("成功任务必须带结果且不能带错误")
    if status == "failed" and (error is None or result is not None):
        raise RuntimeError("失败任务必须带错误且不能带结果")
    conn = _connection_locked()
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = conn.execute(
            "SELECT status, entered_running FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if current is None or current["status"] != "running" or current["entered_running"] != 1:
            raise sqlite3.OperationalError("任务必须从 running 进入终态")
        updated = conn.execute(
            """
            UPDATE jobs
            SET status = ?, updated_at = ?, finished_at = ?,
                result_json = ?, error_json = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                status,
                stamp,
                stamp,
                None if result is None else json.dumps(result, ensure_ascii=False),
                None if error is None else json.dumps(error, ensure_ascii=False),
                job_id,
            ),
        )
        if updated.rowcount != 1:
            raise sqlite3.OperationalError("任务未能进入终态")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


class _Missing:
    """区分「字段缺失」和 JSON null。"""


_MISSING = _Missing()


def _optional_idempotency_key(body: dict[str, Any]) -> tuple[str | None, Rejected | None]:
    if "idempotency_key" not in body or body["idempotency_key"] is None:
        return None, None
    value = body["idempotency_key"]
    if not isinstance(value, str):
        return None, Rejected(400, "bad_request", "idempotency_key 必须是不超过 128 个字符的可见 ASCII 字符串。")
    if value == "":
        return None, None
    if len(value) > _MAX_IDEMPOTENCY_CHARS or any(ord(char) < 33 or ord(char) > 126 for char in value):
        return None, Rejected(
            400,
            "bad_request",
            "idempotency_key 必须是不超过 128 个字符的可见 ASCII 字符串。",
        )
    return value, None


def _optional_client_ref(body: dict[str, Any]) -> tuple[str | None, Rejected | None]:
    if "client_ref" not in body or body["client_ref"] is None:
        return None, None
    value = body["client_ref"]
    if not isinstance(value, str) or len(value) > _MAX_CLIENT_REF_CHARS:
        return None, Rejected(400, "bad_request", "client_ref 必须是不超过 256 个字符的字符串。")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None, Rejected(400, "bad_request", "client_ref 必须是不超过 256 个字符的字符串。")
    return value, None


def _valid_echo(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= _MAX_ECHO_CHARS
