#!/usr/bin/env python3
"""供打包循环写入心跳与启动身份。看门狗只读这些文件。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from watchdog import proc_starttime

CN = ZoneInfo("Asia/Shanghai")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--init-identity", action="store_true")
    args = parser.parse_args(argv)
    logs = args.home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    starttime = proc_starttime(args.pid, args.proc_root) or ""
    now = datetime.now(CN)
    beat = {
        "pid": args.pid,
        "starttime": starttime,
        "phase": args.phase,
        "beat_epoch": now.timestamp(),
        "beat_at": now.isoformat(timespec="seconds"),
    }
    (logs / "heartbeat.json").write_text(
        json.dumps(beat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.init_identity:
        identity = {
            "pid": args.pid,
            "starttime": starttime,
            "started_at": beat["beat_at"],
            "cmdline_marker": "ensure_auto_cn_push_loop.sh",
        }
        (logs / "loop_identity.json").write_text(
            json.dumps(identity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (logs / "ensure_auto_cn_push.pid").write_text(f"{args.pid}\n", encoding="ascii")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
