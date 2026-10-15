#!/usr/bin/env python3
"""离线回填机构赛果表 market_settle。

默认只统计、不创建表、不写任何行。必须同时给出数据库路径和 --apply 才会落库。
写正式库之前，还要先备份，并设置环境变量 FORMAL_DB_BACKUP_DONE=1。
本脚本不会打开双写，也不会修改预测结算。
完场入库和临盘写入成功后，会调用同一套 apply_backfill 做单场覆盖。本脚本仍是离线全量或指定场次的入口。

用法：
  python api/scripts/backfill_market_settle.py --db /path/to/copy.db
  python api/scripts/backfill_market_settle.py --db /path/to/copy.db --apply
  python api/scripts/backfill_market_settle.py --db /path/to/copy.db --jingcai-date 2026-10-15 --apply
  python api/scripts/backfill_market_settle.py --db /path/to/copy.db --match-id 12 --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.formal_db import guard_write_target  # noqa: E402
from app.market_settle import apply_backfill, plan_backfill  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="离线计算机构赛果。不带 --apply 时只打印统计，不写数据库。",
    )
    parser.add_argument("--db", required=True, help="目标 SQLite 数据库的路径。没有默认库，避免误写正式库。")
    parser.add_argument("--apply", action="store_true", help="真正创建表并覆盖写入。缺省时不写库。")
    parser.add_argument("--jingcai-date", default=None, help="只处理这一个竞彩日，格式 YYYY-MM-DD。")
    parser.add_argument("--match-id", action="append", type=int, default=None, help="只处理这些 match_id，可重复传入。")
    args = parser.parse_args(argv)

    path = guard_write_target(args.db, writing=bool(args.apply))
    if not path.exists():
        print(f"找不到数据库：{path}", file=sys.stderr)
        return 2

    if args.apply:
        conn = sqlite3.connect(str(path))
    else:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if args.apply:
            summary = apply_backfill(conn, match_ids=args.match_id, jingcai_date=args.jingcai_date)
            conn.commit()
        else:
            summary = plan_backfill(conn, match_ids=args.match_id, jingcai_date=args.jingcai_date)["summary"]
            summary["dry_run"] = True
            summary["note"] = "这次没有写数据库。确认统计后再加上 --apply。"
    finally:
        conn.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
