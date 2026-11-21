"""从一条已经打开的 SQLite 连接读取库身份。

身份来自这条连接执行的只读查询，不是对路径做 sha256。
同一路径上的旧句柄在文件被替换后仍会报出替换前的内容。
"""
from __future__ import annotations

import hashlib
import sqlite3


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def connection_fingerprint(conn: sqlite3.Connection) -> str:
    """把当前连接看得见的结构与首尾行收成稳定摘要。"""
    lines: list[str] = []
    for pragma in (
        "page_size",
        "page_count",
        "freelist_count",
        "user_version",
        "schema_version",
        "application_id",
        "journal_mode",
    ):
        value = conn.execute(f"PRAGMA {pragma}").fetchone()[0]
        lines.append(f"{pragma}={value}")
    tables: list[str] = []
    rows = conn.execute(
        "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    for typ, name, sql in rows:
        lines.append(f"master|{typ}|{name}|{sql}")
        if typ == "table":
            tables.append(name)
    for name in tables:
        quoted = _quote(name)
        count, max_rowid = conn.execute(
            f"SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM {quoted}"
        ).fetchone()
        lines.append(f"span|{name}|{count}|{max_rowid}")
        for label, direction in (("first", "ASC"), ("last", "DESC")):
            row = conn.execute(
                f"SELECT rowid, * FROM {quoted} ORDER BY rowid {direction} LIMIT 1"
            ).fetchone()
            if row is None:
                lines.append(f"{label}|{name}|")
                continue
            rendered = "|".join("" if value is None else str(value) for value in row)
            lines.append(f"{label}|{name}|{rendered}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def read_identity(conn: sqlite3.Connection) -> dict:
    """报告这条连接正在读的库。不计算、不返回文件字节的 sha256。"""
    conn.execute("PRAGMA query_only = ON")
    opened = ""
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main" and file:
            opened = file
    journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    return {
        "ok": True,
        "read_only": True,
        "fingerprint_source": "running_api",
        "fingerprint": connection_fingerprint(conn),
        "db_path": opened,
        "journal_mode": journal,
    }
