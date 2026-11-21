#!/usr/bin/env python3
"""把已有整库回执迁移成逐日证据。

没有与回执哈希一致的历史快照时，不写已推标记，也不清掉已有缺口。
当前库只能记成当前证据。历史判断不读取今天的 live 撤场目录。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from check_close_complete import evaluate
from run_auto_cn_push import PrepareLock, home_paths, load_json, merge_gap_ledger, now_cn, write_json


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migrate_receipt(
    *,
    home: Path,
    receipt: dict,
    snapshot: Path | None,
    live_root: Path | None,
    today: date,
    start: date,
    as_current: bool = False,
) -> dict:
    paths = home_paths(home)
    paths["markers"].mkdir(parents=True, exist_ok=True)
    report = {
        "kind": "historical_migration",
        "receipt_jc_day": receipt.get("jc_day"),
        "receipt_db_sha256": receipt.get("db_sha256"),
        "receipt_gz_sha256": receipt.get("gz_sha256"),
        "receipt_pushed_at": receipt.get("pushed_at"),
        "receipt_source": receipt.get("source"),
        "fabricated": False,
        "marked": [],
        "gaps": [],
        "no_matches": [],
    }
    lock = PrepareLock(paths["prepare_lock"])
    if not lock.acquire():
        report["status"] = "lock_held"
        return report
    try:
        if snapshot is None:
            report["status"] = "needs_evidence"
            report["note"] = "没有当时的快照，不能把回执日期写成中间日的历史证据"
            write_json(paths["markers"] / "MIGRATION_NEEDS_EVIDENCE.json", report)
            return report
        if as_current:
            return _write_current_evidence(home, report, snapshot, today, start, receipt)
        snap_sha = sha256_file(snapshot)
        report["snapshot_db_sha256"] = snap_sha
        if snap_sha != str(receipt.get("db_sha256") or "").lower():
            report["status"] = "snapshot_hash_mismatch"
            report["note"] = "快照哈希与回执不一致，不把当前结果写成当时的证据"
            write_json(paths["markers"] / "MIGRATION_NEEDS_EVIDENCE.json", report)
            return report
        historical_live = paths["markers"] / ".no-live-snapshot"
        historical_live.mkdir(parents=True, exist_ok=True)
        report["dropped_from_csl_snapshot"] = "absent"
        return _write_days(
            home, report, snapshot, historical_live, today, start, receipt,
            evidence_source="historical_snapshot", historical=True,
        )
    finally:
        lock.release()


def _write_current_evidence(home, report, snapshot, today, start, receipt):
    """当前库只留下不能被当成已推的证据文件。"""
    paths = home_paths(home)
    end = date.fromisoformat(str(receipt["jc_day"]))
    empty = paths["markers"] / ".no-live-snapshot"
    empty.mkdir(parents=True, exist_ok=True)
    day = start
    while day <= end and day <= today:
        row = evaluate(day.isoformat(), snapshot, empty, False)
        write_json(paths["markers"] / f"evidence_current_{day.isoformat()}.json", {
            "jc_day": day.isoformat(),
            "complete": bool(row.get("complete")),
            "reason": row.get("reason"),
            "n_matches": row.get("n_matches"),
            "evidence_source": "current_snapshot",
            "historical": False,
            "not_pushed": True,
            "note": "当前库证据。没有对应投递回执，不是已推标记",
        })
        kind = "no_matches" if (row.get("reason") == "no_matches" or row.get("n_matches") == 0) else (
            "complete" if row.get("complete") else "gaps"
        )
        report.setdefault(kind, []).append(day.isoformat())
        day += timedelta(days=1)
    report["status"] = "current_evidence_not_pushed"
    report["marked"] = []
    write_json(paths["markers"] / "MIGRATION_REPORT.json", report)
    return report


def _write_days(home, report, snapshot, live_root, today, start, receipt, *, evidence_source, historical):
    if not historical:
        raise RuntimeError("当前证据不能写成已推标记")
    if not receipt.get("gz_sha256"):
        raise RuntimeError("没有投递回执的 gz，不能晋升为已推")
    paths = home_paths(home)
    end = date.fromisoformat(str(receipt["jc_day"]))
    planned = []
    day = start
    while day <= end and day <= today:
        row = evaluate(day.isoformat(), snapshot, live_root, False)
        planned.append((day, row))
        day += timedelta(days=1)
    for day, row in planned:
        marker = paths["markers"] / f"pushed_{day.isoformat()}.json"
        if not marker.is_file() or not row.get("complete"):
            continue
        try:
            existing = load_json(marker, {}) or {}
        except Exception:
            report["status"] = "marker_conflict"
            report["conflict_day"] = day.isoformat()
            return report
        same = (
            existing.get("jc_day") == day.isoformat()
            and str(existing.get("db_sha256") or "").lower() == sha256_file(snapshot)
            and existing.get("evidence_source") == evidence_source
            and existing.get("receipt_gz_sha256") == receipt.get("gz_sha256")
        )
        if not same:
            report["status"] = "marker_conflict"
            report["conflict_day"] = day.isoformat()
            return report
    reports = []
    gaps = []
    for day, row in planned:
        evidence = {
            "jc_day": day.isoformat(),
            "complete": bool(row.get("complete")),
            "reason": row.get("reason"),
            "n_matches": row.get("n_matches"),
            "pin_close_ok": row.get("pin_close_ok"),
            "missing_match_uids": row.get("missing_match_uids") or [],
            "evidence_source": evidence_source,
            "evidence_db_sha256": sha256_file(snapshot),
            "historical": historical,
            "receipt_pushed_at": receipt.get("pushed_at"),
            "receipt_source": receipt.get("source"),
        }
        reports.append({"jc_day": day.isoformat(), "complete": bool(row.get("complete")), "reason": row.get("reason"), "n_matches": row.get("n_matches")})
        if row.get("reason") == "no_matches" or row.get("n_matches") == 0:
            report["no_matches"].append(day.isoformat())
            write_json(paths["markers"] / f"checked_no_matches_{day.isoformat()}.json", {
                **evidence,
                "reason": "no_matches",
                "n_matches": 0,
                "kind": "checked_not_pushed",
            })
        elif row.get("complete"):
            marker = paths["markers"] / f"pushed_{day.isoformat()}.json"
            if marker.is_file():
                report["marked"].append(day.isoformat())
                continue
            write_json(marker, {
                "jc_day": day.isoformat(),
                "pushed_at": receipt.get("pushed_at") if historical else now_cn().isoformat(),
                "db_sha256": evidence["evidence_db_sha256"],
                "gz_sha256": receipt.get("gz_sha256") if historical else None,
                "receipt_gz_sha256": receipt.get("gz_sha256"),
                "receipt_pushed_at": receipt.get("pushed_at"),
                "receipt_source": receipt.get("source"),
                "delivery": evidence_source,
                "evidence_source": evidence_source,
                "evidence_db_sha256": evidence["evidence_db_sha256"],
                "historical": historical,
                "coverage_evidence": evidence,
                "not_a_new_push": True,
            })
            report["marked"].append(day.isoformat())
        else:
            gaps.append(evidence)
            report["gaps"].append(evidence)
    merge_gap_ledger(home, reports, gaps)
    report["status"] = "migrated_from_snapshot" if historical else "current_evidence"
    write_json(paths["markers"] / "MIGRATION_REPORT.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--live-root", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--as-current", action="store_true")
    args = parser.parse_args(argv)
    receipt = json.loads(args.receipt.read_text(encoding="utf-8-sig"))
    report = migrate_receipt(
        home=args.home,
        receipt=receipt,
        snapshot=args.snapshot,
        live_root=args.live_root,
        today=now_cn().date(),
        start=date.fromisoformat(args.start),
        as_current=args.as_current,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] not in {"snapshot_hash_mismatch", "marker_conflict", "lock_held"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
