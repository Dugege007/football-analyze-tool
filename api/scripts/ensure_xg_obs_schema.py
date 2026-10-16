#!/usr/bin/env python3
"""补齐预期进球六列要用的表。默认只打印语句，不写任何库。

写入时必须显式传入 --apply 和 --db。写正式库之前要先备份，并设置环境变量
FORMAL_DB_BACKUP_DONE=1。本变更在合并之前不应对正式库执行 --apply。

用法：
  python api/scripts/ensure_xg_obs_schema.py --db /path/to/app.db
  python api/scripts/ensure_xg_obs_schema.py --db /path/to/app.db --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import formal_db  # noqa: E402
from app.xg_obs import _SCHEMA_PATH, ensure_xg_schema  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="补齐预期进球观测表、BSD 赛事映射和拉取队列。默认不写库。")
    parser.add_argument("--db", required=True, help="目标库的路径。不设默认值，避免误写正式库。")
    parser.add_argument("--apply", action="store_true", help="真正写库。省略时只打印将要执行的语句。")
    args = parser.parse_args(argv)
    target = formal_db.guard_write_target(args.db, writing=bool(args.apply))
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    if not args.apply:
        print(sql)
        print(f"干跑结束，没有写入 {target}。确认备份之后再加 --apply。")
        return 0
    conn = sqlite3.connect(target)
    try:
        ensure_xg_schema(conn)
        conn.commit()
    finally:
        conn.close()
    print(f"已写入 {target}。没有改动预期进球数值，只补齐了表。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
