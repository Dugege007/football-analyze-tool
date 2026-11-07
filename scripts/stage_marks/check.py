#!/usr/bin/env python3
"""巡检入口：只读阶段完成标记与实时采集心跳，不扫库、不做缺口重扫。

用法：
  python3 scripts/stage_marks/check.py
  python3 scripts/stage_marks/check.py --profile live
  python3 scripts/stage_marks/check.py --profile all --json

退出码：0 = 所需阶段均新鲜且非 fail；1 = 有过期／失败／缺失；2 = 用法错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 允许直接 python3 scripts/stage_marks/check.py
_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from stage_marks.mark import (  # noqa: E402
    STAGE_DEFS,
    age_minutes,
    is_fresh,
    list_latest_marks,
    marks_root,
    now_cn,
    parse_finished_at,
    read_stage_mark,
    iso_cn,
)

HEARTBEAT_DEFAULT = Path("/workspace/odds-data/5dollar/live/logs/heartbeat.json")
# 进度心跳超过该分钟数视为不新鲜（与 15 分钟唤醒对齐，略放宽）
HEARTBEAT_FRESH_MINUTES = 12.0

# 遗留状态文件（统一标记缺失时作只读回退，不代替写路径）
LEGACY = {
    "fc3d.run": Path("/workspace/fc3d/monitor/last_run.json"),
    "backup.daily": Path("/workspace/backups/football/last_backup.json"),
    "hist.scan_gaps": Path(
        "/workspace/odds-data/backfill/5df-fullmatch-history-queue/queue/state.json"
    ),
}


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def read_heartbeat(path: Path = HEARTBEAT_DEFAULT) -> dict[str, Any]:
    raw = _load_json(path) or {}
    ts = raw.get("ts")
    age = None
    fresh = False
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                from stage_marks.mark import TZ_CN

                dt = dt.replace(tzinfo=TZ_CN)
            age = max(0.0, (now_cn() - dt.astimezone(now_cn().tzinfo)).total_seconds() / 60.0)
            # 有进度 ts 且年龄合格即新鲜；tick_error 且仍带 error 视为不健康
            fresh = age <= HEARTBEAT_FRESH_MINUTES
            if raw.get("phase") == "tick_error" and raw.get("error"):
                fresh = False
        except ValueError:
            pass
    return {
        "path": str(path),
        "ts": ts,
        "watchdog_ts": raw.get("watchdog_ts"),
        "phase": raw.get("phase"),
        "pid": raw.get("pid"),
        "error": raw.get("error"),
        "age_minutes": None if age is None else round(age, 2),
        "fresh": fresh,
        "fresh_minutes_limit": HEARTBEAT_FRESH_MINUTES,
    }


def _legacy_as_mark(stage: str) -> dict[str, Any] | None:
    """把既有 last_run / last_backup / hist state / CN markers 映射成只读伪标记，便于过渡。"""
    if stage == "cn.push":
        markers = Path("/workspace/football-analyze-tool-ops/auto-cn-push-after-close-20261020/markers")
        pushed = sorted(markers.glob("pushed_*.json")) if markers.is_dir() else []
        if not pushed:
            return None
        latest = pushed[-1]
        obj = _load_json(latest) or {}
        finished = obj.get("pushed_at")
        if not finished:
            return None
        return {
            "schema": "stage_mark/v1-legacy-adapter",
            "stage": stage,
            "status": "ok",
            "finished_at": finished,
            "tz": "Asia/Shanghai",
            "summary": {"jc_day": obj.get("jc_day"), "legacy": True, "path": str(latest)},
            "source": "legacy-adapter",
            "refs": {"pushed_marker": str(latest)},
        }
    if stage == "cn.prepare":
        markers = Path("/workspace/football-analyze-tool-ops/auto-cn-push-after-close-20261020/markers")
        pending_path = markers / "pending_push.json"
        obj = _load_json(pending_path)
        if not obj:
            days = sorted(markers.glob("pending_2*.json")) if markers.is_dir() else []
            if not days:
                return None
            pending_path = days[-1]
            obj = _load_json(pending_path)
        if not obj:
            return None
        finished = obj.get("ready_at")
        if not finished:
            return None
        return {
            "schema": "stage_mark/v1-legacy-adapter",
            "stage": stage,
            "status": "ok",
            "finished_at": finished,
            "tz": "Asia/Shanghai",
            "summary": {
                "jc_day": obj.get("jc_day"),
                "legacy": True,
                "path": str(pending_path),
                "note": "pending 仍在或来自最近 pending_DAY；真正推送看 cn.push",
            },
            "source": "legacy-adapter",
            "refs": {"pending": str(pending_path)},
        }
    path = LEGACY.get(stage)
    if not path:
        return None
    obj = _load_json(path)
    if not obj:
        return None
    finished = None
    status = "ok"
    summary: dict[str, Any] = {"legacy": True, "path": str(path)}
    if stage == "fc3d.run":
        finished = obj.get("运行时间")
        if finished and "T" not in str(finished):
            # 2026-11-07 13:37:36 → ISO
            finished = str(finished).replace(" ", "T") + "+08:00"
        summary.update(
            {
                "最新已开奖期": obj.get("最新已开奖期"),
                "新抓期数": obj.get("新抓期数"),
                "回放一致": obj.get("回放一致"),
            }
        )
        if obj.get("回放一致") is False:
            status = "fail"
    elif stage == "backup.daily":
        finished = obj.get("written_at") or obj.get("date")
        if finished and isinstance(finished, str) and "T" not in finished and len(finished) == 10:
            finished = finished + "T23:59:00+08:00"
        if isinstance(finished, str) and "Asia/Shanghai" in finished:
            # "2026-11-07 17:16:32 Asia/Shanghai UTC+8"
            parts = finished.split(" Asia/Shanghai")[0].strip().replace(" ", "T")
            finished = parts + "+08:00"
        summary.update(
            {
                "date": obj.get("date"),
                "matches": obj.get("matches"),
                "integrity": obj.get("integrity"),
                "sha256": (obj.get("sha256") or "")[:16] or None,
            }
        )
        if obj.get("integrity") and obj.get("integrity") != "ok":
            status = "fail"
    elif stage == "hist.scan_gaps":
        finished = obj.get("built_at") or obj.get("last_progress_at")
        summary.update(
            {
                "pending": obj.get("pending"),
                "done": obj.get("done"),
                "last_stop": obj.get("last_stop"),
                "hist_mode": obj.get("hist_mode"),
            }
        )
    if not finished:
        return None
    return {
        "schema": "stage_mark/v1-legacy-adapter",
        "stage": stage,
        "status": status,
        "finished_at": finished if "+" in str(finished) or "Z" in str(finished) else str(finished),
        "tz": "Asia/Shanghai",
        "summary": summary,
        "source": "legacy-adapter",
        "refs": {"legacy_path": str(path)},
    }


def stages_for_profile(profile: str) -> list[str]:
    profile = profile.strip().lower()
    if profile == "all":
        return list(STAGE_DEFS.keys())
    if profile == "live":
        return ["live.tick", "live.open_batch", "live.mid_batch", "live.close_batch", "live.day_summary"]
    if profile == "hist":
        return ["hist.scan_gaps", "hist.pull"]
    if profile == "cn":
        return ["cn.prepare", "cn.push"]
    if profile == "daily":
        return ["fc3d.run", "backup.daily", "hist.scan_gaps"]
    # unknown → all known
    return list(STAGE_DEFS.keys())


def evaluate(profile: str = "all", *, use_legacy: bool = True) -> dict[str, Any]:
    now = now_cn()
    hb = read_heartbeat()
    wanted = stages_for_profile(profile)
    stages_out = []
    stale_or_fail = []
    missing = []

    for stage in wanted:
        mark = read_stage_mark(stage)
        if mark is None and use_legacy:
            mark = _legacy_as_mark(stage)
        meta = STAGE_DEFS.get(stage, {})
        fresh_limit = float(meta.get("fresh_minutes", 36 * 60))
        # 各画像硬要求（其余为参考项，过期不拉低 ok）
        if profile == "live":
            required = stage == "live.tick"
        elif profile == "cn":
            # 有 pending 看 prepare；推送成功看 push。二者都列硬要求，缺一则提示。
            required = stage in ("cn.prepare", "cn.push")
        elif profile == "hist":
            required = stage == "hist.scan_gaps"
        elif profile == "daily":
            required = stage in ("fc3d.run", "backup.daily")
        elif profile == "all":
            required = stage in ("live.tick", "hist.scan_gaps", "fc3d.run", "backup.daily")
        else:
            required = stage in meta.get("required_for_profiles", [])

        if mark is None:
            entry = {
                "stage": stage,
                "label": meta.get("label", stage),
                "present": False,
                "required": required,
                "fresh": False,
                "status": None,
                "age_minutes": None,
                "finished_at": None,
                "summary": {},
            }
            stages_out.append(entry)
            if required:
                missing.append(stage)
            continue

        age = age_minutes(mark, now=now)
        fresh = is_fresh(mark, fresh_minutes=fresh_limit, now=now)
        entry = {
            "stage": stage,
            "label": meta.get("label", stage),
            "present": True,
            "required": required,
            "fresh": fresh,
            "status": mark.get("status"),
            "age_minutes": None if age is None else round(age, 2),
            "finished_at": mark.get("finished_at"),
            "summary": mark.get("summary") or {},
            "source": mark.get("source"),
            "fresh_minutes_limit": fresh_limit,
            "schema": mark.get("schema"),
        }
        stages_out.append(entry)
        if required and (not fresh or mark.get("status") == "fail"):
            stale_or_fail.append(stage)

    # live 画像额外要求心跳新鲜
    heartbeat_required = profile in ("live", "all")
    # 过渡：心跳新鲜但 live.tick 尚未写出时，不把 live.tick 当成硬失败（daemon 换码后会补上）
    soft_missing = []
    if hb.get("fresh") and "live.tick" in missing:
        missing = [s for s in missing if s != "live.tick"]
        soft_missing.append("live.tick")
    ok = True
    if heartbeat_required and not hb.get("fresh"):
        ok = False
    if missing or stale_or_fail:
        ok = False

    return {
        "checked_at": iso_cn(now),
        "profile": profile,
        "marks_root": str(marks_root()),
        "ok": ok,
        "heartbeat": hb,
        "heartbeat_required": heartbeat_required,
        "stages": stages_out,
        "stale_or_fail": stale_or_fail,
        "missing_required": missing,
        "soft_missing": soft_missing,
        "note": "本入口只读标记与心跳，不做近 7 日全库缺口重扫。漏打标抽查交给每天中午前历史补数守卫。",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="检查取数阶段完成标记与心跳新鲜度（不扫库）")
    ap.add_argument(
        "--profile",
        default="all",
        choices=["all", "live", "hist", "cn", "daily"],
        help="live=实时；hist=历史；cn=大陆站；daily=3D+备份；all=全部",
    )
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    ap.add_argument("--no-legacy", action="store_true", help="不回退读 last_run/last_backup 等遗留文件")
    args = ap.parse_args(argv)

    report = evaluate(args.profile, use_legacy=not args.no_legacy)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"checked_at={report['checked_at']} profile={report['profile']} ok={report['ok']}")
        hb = report["heartbeat"]
        print(
            f"heartbeat fresh={hb.get('fresh')} age_min={hb.get('age_minutes')} "
            f"phase={hb.get('phase')} ts={hb.get('ts')}"
        )
        for s in report["stages"]:
            flag = "OK" if s.get("fresh") and s.get("status") != "fail" else (
                "MISS" if not s.get("present") else "STALE" if s.get("status") != "fail" else "FAIL"
            )
            req = "req" if s.get("required") else "opt"
            print(
                f"  [{flag}/{req}] {s['stage']}: status={s.get('status')} "
                f"age_min={s.get('age_minutes')} finished_at={s.get('finished_at')}"
            )
        if report["missing_required"]:
            print("missing_required:", ", ".join(report["missing_required"]))
        if report["stale_or_fail"]:
            print("stale_or_fail:", ", ".join(report["stale_or_fail"]))
        print(report["note"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
