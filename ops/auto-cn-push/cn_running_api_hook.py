#!/usr/bin/env python3
"""生产激活/回滚钩子。向正在运行的接口要只读库身份，再和本机只读连接比对。

对上之后才计算 FAT_CN_DB 的文件 sha256，并声明 fingerprint_source=running_api。
连不上、路径不一致、日志模式不是 delete、旁边有 wal/shm、或查询摘要不一致时直接失败。
不读取预期哈希，也不把磁盘摘要标成运行侧证据。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_API_URL = "http://127.0.0.1:8787/health/db"
_TOKEN_HEADER = "X-Fat-Health-Db-Token"


def fail(token: str) -> None:
    sys.stderr.write(token + "\n")
    raise SystemExit(1)


def app_root() -> Path:
    configured = os.environ.get("FAT_CN_APP_ROOT", "").strip()
    if configured:
        root = Path(configured)
    else:
        root = Path(__file__).resolve().parents[2] / "api"
    if not (root / "app" / "running_db.py").is_file():
        fail("APP_ROOT_MISSING")
    return root


def load_identity():
    root = app_root()
    sys.path.insert(0, str(root))
    from app.running_db import read_identity

    return read_identity


def api_token() -> str:
    configured = os.environ.get("FAT_HEALTH_DB_TOKEN_FILE", "").strip()
    if not configured:
        fail("TOKEN_MISSING")
    try:
        token = Path(configured).read_text(encoding="utf-8").strip()
    except OSError:
        fail("TOKEN_MISSING")
    if not token:
        fail("TOKEN_MISSING")
    return token


def fetch_remote(url: str) -> dict:
    if not url:
        fail("API_URL_MISSING")
    timeout = float(os.environ.get("FAT_HEALTH_DB_TIMEOUT", "60") or "60")
    request = urllib.request.Request(url, method="GET", headers={_TOKEN_HEADER: api_token()})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                fail("IDENTITY_REFUSED")
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        fail("IDENTITY_REFUSED" if exc.code in (401, 403, 405) else "API_UNREACHABLE")
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError):
        fail("API_UNREACHABLE")
    if not isinstance(payload, dict):
        fail("IDENTITY_REFUSED")
    return payload


def local_identity(db: Path, read_identity) -> dict:
    uri = db.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        fail("LOCAL_DB_UNREADABLE")
    try:
        try:
            return read_identity(conn)
        except sqlite3.Error:
            fail("LOCAL_DB_UNREADABLE")
    finally:
        conn.close()


def main() -> None:
    raw_db = os.environ.get("FAT_CN_DB", "").strip()
    if not raw_db:
        fail("DB_PATH_MISSING")
    db = Path(raw_db)
    if not db.is_file():
        fail("DB_PATH_MISSING")
    url = os.environ.get("FAT_CN_API_URL", DEFAULT_API_URL).strip()
    remote = fetch_remote(url)
    if remote.get("ok") is not True or remote.get("read_only") is not True:
        fail("IDENTITY_REFUSED")
    if remote.get("fingerprint_source") != "running_api":
        fail("IDENTITY_REFUSED")
    fingerprint = str(remote.get("fingerprint") or "")
    opened = str(remote.get("db_path") or "")
    journal = str(remote.get("journal_mode") or "").lower()
    if not fingerprint or not opened:
        fail("IDENTITY_REFUSED")
    try:
        if Path(opened).resolve() != db.resolve():
            fail("PATH_MISMATCH")
    except OSError:
        fail("PATH_MISMATCH")
    if journal != "delete":
        fail("WAL_UNVERIFIED")
    if db.with_name(db.name + "-wal").exists() or db.with_name(db.name + "-shm").exists():
        fail("WAL_SIDECAR_PRESENT")
    read_identity = load_identity()
    local = local_identity(db, read_identity)
    if local.get("fingerprint") != fingerprint:
        fail("FINGERPRINT_MISMATCH")
    proof = {
        "ok": True,
        "read_only": True,
        "fingerprint_source": "running_api",
        "db_sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
        "fingerprint": fingerprint,
    }
    sys.stdout.write(json.dumps(proof, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
