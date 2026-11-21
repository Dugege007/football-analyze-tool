#!/usr/bin/env python3
"""把已有整库回执迁移成逐日证据。没有当时的快照时不补造已推标记。"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from check_close_complete import evaluate
from run_auto_cn_push import home_paths, now_cn, write_json


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
    live_root: Path,
    today: date,
    start: date,
) -> dict:
    paths = home_paths(home)
    paths["markers"].mkdir(parents=True, exist_ok=True)
    report = {
        "kind": "historical_migration",
        "receipt_jc_day": receipt.get("jc_day"),
        "receipt_db_sha256": receipt.get("db_sha256"),
        "receipt_gz_sha256": receipt.get("gz_sha256"),
        "fabricated": False,
        "marked": [],
        "gaps": [],
        "no_matches": [],
    }
    if snapshot is None:
        report["status"] = "needs_evidence"
        report["note"] = "回执只有整库日，不能把最大已推日写成中间日的历史证据"
        write_json(paths["markers"] / "MIGRATION_NEEDS_EVIDENCE.json", report)
        return report
    snap_sha = sha256_file(snapshot)
    report["snapshot_db_sha256"] = snap_sha
    if snap_sha != str(receipt.get("db_sha256") or "").lower():
        report["status"] = "snapshot_hash_mismatch"
        report["note"] = "这份快照不是回执对应的库，不能把它现在的齐套结果说成当时的证据"
        write_json(paths["markers"] / "MIGRATION_NEEDS_EVIDENCE.json", report)
        return report
    end = date.fromisoformat(str(receipt["jc_day"]))
    day = start
    while day <= end and day <= today:
        row = evaluate(day.isoformat(), snapshot, live_root, False)
        evidence = {
            "jc_day": day.isoformat(),
            "complete": bool(row.get("complete")),
            "reason": row.get("reason"),
            "n_matches": row.get("n_matches"),
            "pin_close_ok": row.get("pin_close_ok"),
            "missing_match_uids": row.get("missing_match_uids") or [],
            "evidence_source": "historical_snapshot",
            "evidence_db_sha256": snap_sha,
        }
        if row.get("reason") == "no_matches" or row.get("n_matches") == 0:
            report["no_matches"].append(day.isoformat())
            stat = snapshot.stat()
            write_json(paths["markers"] / f"checked_no_matches_{day.isoformat()}.json", {
                **evidence,
                "reason": "no_matches",
                "n_matches": 0,
                "db_mtime_ns": stat.st_mtime_ns,
                "db_size": stat.st_size,
                "kind": "checked_not_pushed",
            })
        elif row.get("complete"):
            write_json(paths["markers"] / f"pushed_{day.isoformat()}.json", {
                "jc_day": day.isoformat(),
                "pushed_at": now_cn().isoformat(),
                "db_sha256": snap_sha,
                "gz_sha256": receipt.get("gz_sha256"),
                "receipt_gz_sha256": receipt.get("gz_sha256"),
                "delivery": "historical_snapshot",
                "evidence_source": "historical_snapshot",
                "evidence_db_sha256": snap_sha,
                "coverage_evidence": evidence,
                "not_a_new_push": True,
                "note": "证据来自与回执哈希一致的历史快照，不是用当前库补写的过去",
            })
            report["marked"].append(day.isoformat())
        else:
            report["gaps"].append(evidence)
        day += timedelta(days=1)
    report["status"] = "migrated_from_snapshot"
    write_json(paths["gap_ledger"], {"kind": "gap_ledger", "days": report["gaps"], "source": "historical_snapshot"})
    write_json(paths["markers"] / "MIGRATION_REPORT.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--live-root", type=Path, required=True)
    parser.add_argument("--start", required=True)
    args = parser.parse_args(argv)
    receipt = json.loads(args.receipt.read_text(encoding="utf-8-sig"))
    report = migrate_receipt(
        home=args.home,
        receipt=receipt,
        snapshot=args.snapshot,
        live_root=args.live_root,
        today=now_cn().date(),
        start=date.fromisoformat(args.start),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] != "snapshot_hash_mismatch" else 2


if __name__ == "__main__":
    raise SystemExit(main())
