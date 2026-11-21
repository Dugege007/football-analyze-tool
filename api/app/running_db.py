"""从一条已经打开的 SQLite 连接读取完整内容身份。

身份覆盖这次只读事务里的全部结构与全部行，使用带类型和长度的规范编码。
不把 query_only 当成只读，也不对路径做 sha256。
"""
from __future__ import annotations

import hashlib
import sqlite3
import struct


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def opened_readonly(conn: sqlite3.Connection) -> bool:
    """主库是否以 mode=ro 打开。可写连接上的语句会回滚，不留下探针表。"""
    conn.execute("BEGIN")
    try:
        try:
            conn.execute('CREATE TABLE "_fat_ro_probe"(x)')
        except sqlite3.OperationalError as exc:
            return "readonly" in str(exc).lower()
        return False
    finally:
        conn.execute("ROLLBACK")


def _add_value(digest: hashlib._Hash, value) -> None:
    if value is None:
        digest.update(b"\x00")
        return
    if isinstance(value, bool):
        digest.update(b"\x05" if value else b"\x06")
        return
    if isinstance(value, int):
        digest.update(b"\x01")
        sign = b"\x01" if value < 0 else b"\x00"
        magnitude = abs(value)
        raw = magnitude.to_bytes(max(1, (magnitude.bit_length() + 7) // 8), "big")
        digest.update(sign)
        digest.update(len(raw).to_bytes(4, "big"))
        digest.update(raw)
        return
    if isinstance(value, float):
        digest.update(b"\x02")
        digest.update(struct.pack(">d", value))
        return
    if isinstance(value, str):
        digest.update(b"\x03")
        raw = value.encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
        return
    if isinstance(value, (bytes, memoryview)):
        digest.update(b"\x04")
        raw = bytes(value)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
        return
    raise TypeError(f"unsupported sqlite value {type(value).__name__}")


def _add_row(digest: hashlib._Hash, values) -> None:
    digest.update(b"\x20")
    digest.update(len(values).to_bytes(4, "big"))
    for value in values:
        _add_value(digest, value)


def _snapshot_hash(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    _add_value(digest, "FATDBID1")
    conn.execute("BEGIN")
    try:
        master = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        digest.update(b"\x10")
        digest.update(len(master).to_bytes(4, "big"))
        tables: list[tuple[str, str]] = []
        for row in master:
            _add_row(digest, tuple(row))
            if row[0] == "table":
                tables.append((row[1], row[3] or ""))
        for name, sql in tables:
            quoted = _quote(name)
            info = conn.execute(f"PRAGMA table_info({quoted})").fetchall()
            digest.update(b"\x11")
            _add_value(digest, name)
            digest.update(len(info).to_bytes(4, "big"))
            primary: list[tuple[int, str]] = []
            for item in info:
                _add_row(digest, tuple(item))
                if item[5]:
                    primary.append((int(item[5]), item[1]))
            without_rowid = "WITHOUT ROWID" in sql.upper()
            if without_rowid:
                if not primary:
                    raise sqlite3.OperationalError(f"missing primary key for {name}")
                order = ", ".join(_quote(column) for _order, column in sorted(primary))
                query = f"SELECT * FROM {quoted} ORDER BY {order}"
            else:
                query = f"SELECT rowid, * FROM {quoted} ORDER BY rowid"
            count = 0
            for record in conn.execute(query):
                count += 1
                _add_row(digest, tuple(record))
            digest.update(b"\x12")
            digest.update(count.to_bytes(8, "big"))
        return digest.hexdigest()
    finally:
        conn.execute("ROLLBACK")


def connection_fingerprint(conn: sqlite3.Connection) -> str:
    """对当前连接的一致只读快照做完整内容摘要。可写连接直接拒绝。"""
    if not opened_readonly(conn):
        raise sqlite3.OperationalError("connection is writable")
    return _snapshot_hash(conn)


def read_identity(conn: sqlite3.Connection) -> dict:
    """报告这条连接正在读的库。不计算、不返回文件字节的 sha256。"""
    opened = ""
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main" and file:
            opened = file
    journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    readonly = opened_readonly(conn)
    body = {
        "ok": readonly,
        "read_only": readonly,
        "fingerprint_source": "running_api",
        "db_path": opened,
        "journal_mode": journal,
    }
    if not readonly:
        return body
    body["fingerprint"] = connection_fingerprint(conn)
    return body
