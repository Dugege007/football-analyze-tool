#!/usr/bin/env python3
"""Online-safe snapshot of formal APP_DB → staging/app_latest.db.gz + sha sidecars."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import struct
import zlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
DEFAULT_DB = Path(os.environ.get("APP_DB_PATH", "/workspace/match-analysis-api/data/app.db"))
OPS = Path(os.environ.get("AUTO_CN_PUSH_HOME", Path(__file__).resolve().parent))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def gzip_commit_id(blob: bytes) -> str:
    """读出打包时写入 gzip 注释的预期提交身份。没有注释就是未绑定的旧包。"""
    if len(blob) < 10 or blob[0] != 0x1F or blob[1] != 0x8B:
        return ""
    flag = blob[3]
    pos = 10
    if flag & 4:
        if pos + 2 > len(blob):
            return ""
        extra = struct.unpack_from("<H", blob, pos)[0]
        pos += 2 + extra
    if flag & 8:
        end = blob.find(b"\0", pos)
        if end < 0:
            return ""
        pos = end + 1
    if flag & 16:
        end = blob.find(b"\0", pos)
        if end < 0:
            return ""
        return blob[pos:end].decode("ascii")
    return ""


def write_db_gzip(raw: bytes, dest: Path, expected_commit_id: str | None = None, mtime: int = 0) -> None:
    """同一库字节、同一预期提交和同一 mtime 得到同一个 gz。身份写在 gzip 注释里。"""
    compressor = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = compressor.compress(raw) + compressor.flush()
    comment = b""
    flag = 0
    if expected_commit_id:
        comment = expected_commit_id.encode("ascii") + b"\0"
        flag = 16
    header = struct.pack("<BBBBLBB", 0x1F, 0x8B, 8, flag, int(mtime) & 0xFFFFFFFF, 0, 255)
    crc = zlib.crc32(raw) & 0xFFFFFFFF
    isize = len(raw) & 0xFFFFFFFF
    dest.write_bytes(header + comment + body + struct.pack("<II", crc, isize))


def build_package(
    db: Path,
    out_dir: Path,
    jc_day: str,
    coverage: dict | None = None,
    expected_commit_id: str | None = None,
) -> dict:
    """只读 online backup。coverage 只写入元数据，不改变快照内容。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_db = out_dir / "app_latest.db"
    gz = out_dir / "app_latest.db.gz"
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if raw_db.exists():
            raw_db.unlink()
        dst = sqlite3.connect(str(raw_db))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    db_sha = sha256_file(raw_db)
    # 预期提交身份在生成这个包时写入，并被 gz 哈希盖住。授权不能事后补。
    write_db_gzip(raw_db.read_bytes(), gz, expected_commit_id or None, mtime=0)
    gz_sha = sha256_file(gz)
    (out_dir / "app_latest.db.sha256").write_text(db_sha + "\n", encoding="ascii")
    (out_dir / "app_latest.db.gz.sha256").write_text(gz_sha + "\n", encoding="ascii")
    meta = {
        "jc_day": jc_day,
        "prepared_at": datetime.now(CN).isoformat(),
        "source_db": str(db),
        "db_bytes": raw_db.stat().st_size,
        "db_sha256": db_sha,
        "gz_bytes": gz.stat().st_size,
        "gz_sha256": gz_sha,
        "live_capture_note": "Do NOT enable LIVE_CAPTURE on CN",
    }
    if expected_commit_id:
        meta["expected_commit_id"] = expected_commit_id
    if coverage is not None:
        meta["coverage"] = coverage
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    raw_db.unlink(missing_ok=True)
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out-dir", type=Path, default=OPS / "staging")
    ap.add_argument("--jc-day", required=True)
    ap.add_argument("--coverage-json", type=Path)
    args = ap.parse_args(argv)
    coverage = None
    if args.coverage_json:
        coverage = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    meta = build_package(args.db, args.out_dir, args.jc_day, coverage)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
