#!/usr/bin/env python3
"""历史完赛场次的预期进球回填。与盘口补数队列分开，单独遵守每天 7500 次的免费额度。

默认是干跑：只统计完赛场次、有映射的场次、已经入库的场次，以及无法获取的原因分布。
干跑不建表、不请求 BSD、不写观测。

真正拉取时加上 --apply，并显式给出 --db。每次运行还有 --limit，请求之间默认间隔 2 秒。
写正式库之前要先备份，并设置环境变量 FORMAL_DB_BACKUP_DONE=1。
本变更在合并之前不应对正式库执行 --apply。

映射写在 bsd_event_map（match_id，event_id）。没有映射的完赛场次记为 no_bsd_mapping，不发请求。

用法：
  python scripts/backfill/xg_bsd_history_queue.py --db /path/to/app.db
  python scripts/backfill/xg_bsd_history_queue.py --db /path/to/app.db --apply --limit 20
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "api"))

from app import formal_db  # noqa: E402
from app import xg_obs  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="历史完赛场次的预期进球回填。默认干跑，不写库。")
    parser.add_argument("--db", required=True, help="目标库的路径。不设默认值，避免误写正式库。")
    parser.add_argument("--apply", action="store_true", help="按额度拉取并写入。省略时只打印分类。")
    parser.add_argument("--limit", type=int, default=20, help="这一次最多请求多少场。默认 20。")
    parser.add_argument("--min-interval", type=float, default=2.0, help="两次请求之间至少间隔的秒数。默认 2。")
    parser.add_argument("--daily-cap", type=int, default=xg_obs.DAILY_CALL_CAP, help="协调世界时每天的请求上限。默认 7500。")
    parser.add_argument("--quota-file", default=None, help="额度账本路径。默认与盘口补数账本分开。")
    args = parser.parse_args(argv)
    if args.limit < 1:
        print("limit 至少为 1。", file=sys.stderr)
        return 2
    target = formal_db.guard_write_target(args.db, writing=bool(args.apply))
    if not target.exists():
        print(f"找不到数据库文件 {target}。请传入已经存在的库路径。", file=sys.stderr)
        return 2
    if not args.apply:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            report = xg_obs.classify_backfill(conn)
        finally:
            conn.close()
        report["dry_run"] = True
        report["note"] = "干跑没有请求 BSD，也没有写入观测。确认之后再加 --apply。"
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report.get("ok") else 2

    now = datetime.now(xg_obs.TZ_CN)
    quota_path = Path(args.quota_file) if args.quota_file else xg_obs.default_quota_path()
    conn = sqlite3.connect(target, timeout=60)
    try:
        xg_obs.ensure_xg_schema(conn)
        pairs = xg_obs.pending_backfill_pairs(conn, args.limit)
        client = xg_obs.client_from_environment()
        if client is None:
            print(json.dumps({
                "dry_run": False,
                "skipped_no_token": True,
                "pending": len(pairs),
                "note": "没有 BSD_BZZOIRO_API_KEY，没有发请求，也没有写入观测。",
            }, ensure_ascii=False, indent=1))
            return 0
        stored = unavailable = errors = 0
        stopped_quota = False
        for index, (match_id, event_id) in enumerate(pairs):
            if not xg_obs.reserve_daily_call(quota_path, now, cap=args.daily_cap):
                stopped_quota = True
                break
            if index > 0 and args.min_interval > 0:
                time.sleep(args.min_interval)
            fetched = client.fetch_event_stats(event_id)
            outcome = xg_obs.store_fetch_result(conn, match_id, event_id, fetched, now)
            conn.commit()
            if outcome == "stored":
                stored += 1
            elif outcome == "unavailable":
                unavailable += 1
            else:
                errors += 1
                if fetched.get("http_status") == 429:
                    stopped_quota = True
                    break
        classification = xg_obs.classify_backfill(conn)
    finally:
        conn.close()
    print(json.dumps({
        "dry_run": False,
        "requested": stored + unavailable + errors,
        "stored": stored,
        "unavailable": unavailable,
        "errors": errors,
        "stopped_quota": stopped_quota,
        "daily_cap": args.daily_cap,
        "min_interval_seconds": args.min_interval,
        "unavailable_reasons": classification.get("unavailable_reasons"),
        "finished_matches": classification.get("finished_matches"),
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
