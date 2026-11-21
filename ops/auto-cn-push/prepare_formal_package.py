#!/usr/bin/env python3
"""Online-safe snapshot of formal APP_DB → staging/app_latest.db.gz + sha sidecars."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import gzip
import shutil
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


def build_package(
    db: Path,
    out_dir: Path,
    jc_day: str,
    coverage: dict | None = None,
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
    # mtime 固定为 0，同一库字节重复打包得到同一个 gz。不要靠改回执 gz 来对齐。
    with raw_db.open("rb") as fin, gz.open("wb") as raw_out:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_out, mtime=0, compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout, length=1024 * 1024)
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
