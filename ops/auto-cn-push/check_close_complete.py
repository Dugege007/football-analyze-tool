#!/usr/bin/env python3
"""Detect whether 平博(pinnacle) rule closing odds are complete for a 竞彩日.

Complete := every jingcai match on jc_day has odds_snapshot
  book=pinnacle, market=asian, channel=rule, point=close, line IS NOT NULL,
  except matches the live plan marked as dropped_from_csl (no longer on the
  jingcai list). Those rows stay in the formal database but do not block the day.

Optionally also require live_capture summary close|rule pending==0
(and captured+asof_backfilled+rescued >= targets).

Prints JSON to stdout. Exit 0 always (use .complete field).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
DEFAULT_DB = Path(
    os.environ.get(
        "APP_DB_PATH",
        "/workspace/match-analysis-api/data/app.db",
    )
)
DEFAULT_LIVE = Path(
    os.environ.get(
        "ODDS_DATA_DIR",
        "/workspace/odds-data",
    )
) / "5dollar/live"


def jc_days(explicit: str | None) -> list[str]:
    if explicit:
        return [explicit]
    today = datetime.now(CN).date()
    # yesterday + today (close often lands evening of jc_day)
    return [(today - timedelta(days=1)).isoformat(), today.isoformat()]


def dropped_from_csl_uids(live_root: Path, jc_day: str) -> list[str]:
    """Match UIDs the live plan recorded as removed from the jingcai list."""
    plan_path = live_root / "plan" / f"{jc_day}.json"
    if not plan_path.is_file():
        return []
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    dropped: list[str] = []
    for entry in data.get("kickoff_log") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("event") != "dropped_from_csl":
            continue
        uid = entry.get("match_uid")
        if isinstance(uid, str) and uid:
            dropped.append(uid)
    return sorted(set(dropped))


def db_coverage(
    con: sqlite3.Connection,
    jc_day: str,
    excluded: set[str] | None = None,
) -> dict:
    excluded = set(excluded or ())
    uids = [
        r[0]
        for r in con.execute(
            """
            SELECT match_uid FROM matches
            WHERE substr(match_uid,1,10)=?
              AND match_uid GLOB '????-??-??|*'
            ORDER BY match_uid
            """,
            (jc_day,),
        ).fetchall()
    ]
    counted = [uid for uid in uids if uid not in excluded]
    excluded_present = [uid for uid in uids if uid in excluded]
    pin_ok_uids = {
        r[0]
        for r in con.execute(
            """
            SELECT DISTINCT m.match_uid FROM matches m
            JOIN odds_snapshot o ON o.match_id=m.id
            WHERE substr(m.match_uid,1,10)=?
              AND m.match_uid GLOB '????-??-??|*'
              AND o.book='pinnacle' AND o.market='asian'
              AND o.channel='rule' AND o.point='close'
              AND o.line IS NOT NULL
            """,
            (jc_day,),
        ).fetchall()
    }
    missing = [uid for uid in counted if uid not in pin_ok_uids]
    pin_ok = sum(1 for uid in counted if uid in pin_ok_uids)
    n_matches = len(counted)
    return {
        "n_matches_in_db": len(uids),
        "n_matches": n_matches,
        "excluded_dropped_from_csl": excluded_present,
        "pin_close_ok": pin_ok,
        "db_complete": n_matches > 0 and not missing,
        "missing_match_uids": missing,
    }


def live_summary(live_root: Path, jc_day: str) -> dict | None:
    p = live_root / "summary" / f"{jc_day}.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}
    phase = (data.get("targets_by_phase") or {}).get("close|rule") or {}
    targets = int(phase.get("targets") or 0)
    captured = int(phase.get("captured") or 0)
    asof = int(phase.get("asof_backfilled") or 0)
    pending = int(phase.get("pending") or 0)
    missed = int(phase.get("missed") or 0)
    no_odds = int(phase.get("no_odds") or 0)
    done = captured + asof
    # treat rescued as done if present in ingest; summary uses asof_backfilled bucket
    live_complete = targets > 0 and pending == 0 and missed == 0 and no_odds == 0 and done >= targets
    pin = ((data.get("core_book_coverage") or {}).get("close|rule") or {}).get("pinnacle")
    return {
        "path": str(p),
        "targets": targets,
        "captured": captured,
        "asof_backfilled": asof,
        "pending": pending,
        "missed": missed,
        "no_odds": no_odds,
        "pinnacle_asian_rows": pin,
        "live_complete": live_complete,
    }


def evaluate(jc_day: str, db: Path, live_root: Path, require_live: bool) -> dict:
    out: dict = {
        "jc_day": jc_day,
        "checked_at": datetime.now(CN).isoformat(),
        "db_path": str(db),
    }
    if not db.is_file():
        out["complete"] = False
        out["error"] = f"missing db {db}"
        return out
    excluded = set(dropped_from_csl_uids(live_root, jc_day))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cov = db_coverage(con, jc_day, excluded)
    finally:
        con.close()
    out.update(cov)
    live = live_summary(live_root, jc_day)
    out["live"] = live
    if cov["n_matches"] == 0:
        out["complete"] = False
        out["reason"] = "no_matches"
        return out
    if require_live:
        out["complete"] = bool(cov["db_complete"] and live and live.get("live_complete"))
        if not out["complete"]:
            out["reason"] = "db_or_live_incomplete"
    else:
        # Prefer DB (what CN will show); live is advisory
        out["complete"] = bool(cov["db_complete"])
        if not out["complete"]:
            out["reason"] = "db_incomplete"
        elif live and not live.get("live_complete"):
            out["live_advisory"] = "live_summary_not_yet_complete"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="竞彩日 YYYY-MM-DD（默认昨天+今天）")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--live-root", type=Path, default=DEFAULT_LIVE)
    ap.add_argument(
        "--require-live",
        action="store_true",
        help="同时要求 live summary close|rule 齐套",
    )
    ap.add_argument("--only-complete", action="store_true")
    args = ap.parse_args(argv)
    results = [evaluate(d, args.db, args.live_root, args.require_live) for d in jc_days(args.date)]
    if args.only_complete:
        results = [r for r in results if r.get("complete")]
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
