#!/usr/bin/env python3
"""安装验收。不健康时退出非 0，并打印原因。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from config_env import ConfigError, load_push_env
from watchdog import assess, observe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--cron-text", default="")
    parser.add_argument("--marker", default="# fat-auto-cn-push-watchdog")
    args = parser.parse_args(argv)
    home = args.home
    try:
        values = load_push_env()
    except ConfigError as exc:
        print(json.dumps({"status": "VERIFY_CONFIG", "reason": str(exc)}, ensure_ascii=False))
        return 5
    os.environ["APP_DB_PATH"] = values["APP_DB_PATH"]
    os.environ["ODDS_DATA_DIR"] = values["ODDS_DATA_DIR"]
    count = args.cron_text.count(args.marker)
    if count != 1:
        print(json.dumps({"status": "VERIFY_CRON", "cron_count": count}, ensure_ascii=False))
        return 4
    decision = assess(observe(home))
    healthy = decision.get("action") == "ok" and decision.get("reason") in {"healthy", "long_op", "long_op_no_heartbeat"}
    if not healthy:
        print(json.dumps({"status": "VERIFY_LOOP", "decision": decision}, ensure_ascii=False))
        return 2
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import run_auto_cn_push

    live_root = Path(values["ODDS_DATA_DIR"]) / "5dollar" / "live"
    try:
        scanned = run_auto_cn_push.scan_and_alert(home=home, db=Path(values["APP_DB_PATH"]), live_root=live_root)
    except Exception as exc:
        print(json.dumps({"status": "VERIFY_SCAN", "error": str(exc)}, ensure_ascii=False))
        return 6
    gaps = (scanned.get("coverage") or {}).get("gap_days") or []
    if gaps or scanned.get("complete_unpushed"):
        print(json.dumps({
            "status": "VERIFY_GAP",
            "gap_days": [item.get("jc_day") for item in gaps],
            "complete_unpushed": scanned.get("complete_unpushed"),
        }, ensure_ascii=False))
        return 3
    print(json.dumps({"status": "VERIFY_OK", "decision": decision.get("reason")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
