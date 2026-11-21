#!/usr/bin/env python3
"""临盘齐套后准备正式库推送包，并在回执哈希一致后标记已推。

打包循环每轮调用 --prepare / --check-alerts。历史齐套未推从已推连续边界之后扫描，
不把最大已推日当作边界。已有 pending 时不覆盖 staging。
实际 scp 仍在 YuePC/MSI 上执行。本脚本不开启 LIVE_CAPTURE，也不拉取赔率 API。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from check_close_complete import DEFAULT_DB, DEFAULT_LIVE, evaluate
from prepare_formal_package import build_package
from protect import (
    contiguous_boundary,
    delivery_decision,
    norm_hash,
    scan_days,
    select_coverage,
)

CN = ZoneInfo("Asia/Shanghai")
OPS = Path(os.environ.get("AUTO_CN_PUSH_HOME", Path(__file__).resolve().parent))
PENDING_TIMEOUT_HOURS = 2
STAGING_FILES = (
    "app_latest.db.gz",
    "app_latest.db.sha256",
    "app_latest.db.gz.sha256",
    "meta.json",
)


def home_paths(home: Path) -> dict[str, Path]:
    markers = home / "markers"
    return {
        "markers": markers,
        "staging": home / "staging",
        "pending": markers / "pending_push.json",
        "alert_pending": markers / "ALERT_PENDING_TIMEOUT.json",
        "alert_ack": markers / "ALERT_PENDING_ACK.json",
        "alert_stale": markers / "ALERT_STALE_INCOMPLETE.json",
        "alert_stale_ack": markers / "ALERT_STALE_INCOMPLETE_ACK.json",
        "alert_unpushed": markers / "ALERT_COMPLETE_UNPUSHED.json",
        "prepare_lock": home / "logs" / "prepare.lock",
    }


def load_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_cn() -> datetime:
    raw = os.environ.get("AUTO_CN_PUSH_NOW", "").strip()
    if raw:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CN)
        return parsed.astimezone(CN)
    return datetime.now(CN)


def lookback_days() -> int:
    raw = os.environ.get("AUTO_CN_PUSH_LOOKBACK_DAYS", "14").strip()
    return int(raw)


def _stage_mark(stage: str, *, status: str = "ok", summary=None, refs=None) -> None:
    """统一阶段标记；失败不影响打包／推送主路径。"""
    try:
        scripts_candidates = []
        env_scripts = os.environ.get("STAGE_MARKS_SCRIPTS", "").strip()
        if env_scripts:
            scripts_candidates.append(env_scripts)
        scripts_candidates.append(str(Path(__file__).resolve().parents[2] / "scripts"))
        scripts_candidates.append("/workspace/football-analyze-tool/scripts")
        for scripts in scripts_candidates:
            if scripts and (Path(scripts) / "stage_marks").is_dir():
                if scripts not in sys.path:
                    sys.path.insert(0, scripts)
                break
        from stage_marks.mark import write_stage_mark

        write_stage_mark(
            stage,
            status=status,
            summary=summary or {},
            source="ops/auto-cn-push/run_auto_cn_push.py",
            refs=refs or {},
        )
    except Exception as exc:
        print(f"[stage_mark_warn] {stage}: {exc}", flush=True)


def marker_path(markers: Path, jc_day: str) -> Path:
    return markers / f"pushed_{jc_day}.json"


def pushed_days(markers: Path) -> set[str]:
    found: set[str] = set()
    if not markers.is_dir():
        return found
    for path in markers.glob("pushed_*.json"):
        day = path.stem.removeprefix("pushed_")
        if len(day) == 10 and day[4] == "-" and day[7] == "-":
            found.add(day)
    return found


def pending_age_hours(pend: dict, now: datetime) -> float | None:
    ready = pend.get("ready_at")
    if not ready:
        return None
    try:
        ts = datetime.fromisoformat(ready)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=CN)
    return (now - ts).total_seconds() / 3600.0


class PrepareLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            return False

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None


def check_day(jc_day: str, db: Path, live_root: Path, require_live: bool = False) -> dict:
    return evaluate(jc_day, db, live_root, require_live)


def plan_coverage(
    *,
    markers: Path,
    db: Path,
    live_root: Path,
    today,
    lookback: int,
    require_live: bool = False,
    check_fn=None,
) -> dict:
    pushed = pushed_days(markers)
    boundary = contiguous_boundary(pushed)
    days = scan_days(boundary, today, lookback)
    checker = check_fn or (lambda day: check_day(day, db, live_root, require_live))
    reports = []
    for day in days:
        row = checker(day)
        if not isinstance(row, dict):
            raise RuntimeError(f"check failed for {day}")
        if row.get("error"):
            raise RuntimeError(row["error"])
        reports.append(row)
    coverage = select_coverage(reports, pushed=pushed, today=today, boundary=boundary)
    return {"pushed": sorted(pushed), "coverage": coverage, "reports": reports}


def _past_incomplete(reports: list[dict], today, pushed: set[str]) -> list[dict]:
    rows = []
    for row in reports:
        day = str(row.get("jc_day") or "")
        if day in pushed:
            continue
        try:
            current = date.fromisoformat(day)
        except ValueError:
            continue
        if current >= today:
            continue
        if row.get("complete"):
            continue
        if row.get("reason") == "no_matches" or row.get("n_matches") == 0:
            continue
        rows.append(
            {
                "jc_day": day,
                "reason": row.get("reason"),
                "n_matches": row.get("n_matches"),
                "n_matches_in_db": row.get("n_matches_in_db"),
                "pin_close_ok": row.get("pin_close_ok"),
                "missing_match_uids": row.get("missing_match_uids") or [],
                "excluded_dropped_from_csl": row.get("excluded_dropped_from_csl") or [],
            }
        )
    return rows


def update_complete_unpushed_alert(home: Path, coverage: dict, pending_exists: bool, now: datetime) -> dict:
    paths = home_paths(home)
    claimed = coverage.get("claimed_jc_days") or []
    if pending_exists or not claimed:
        if paths["alert_unpushed"].is_file():
            paths["alert_unpushed"].unlink()
        return {"complete_unpushed": None, "cleared": True}
    payload = {
        "kind": "complete_unpushed",
        "claimed_jc_days": claimed,
        "boundary": coverage.get("boundary"),
        "blocking_gap": coverage.get("blocking_gap"),
        "raised_at": now.isoformat(),
        "note": "已齐套但未推，且当前没有 pending。循环应补打整库包；未补打时保留本告警",
    }
    prev = load_json(paths["alert_unpushed"], {}) or {}
    if prev.get("claimed_jc_days") == claimed:
        payload["raised_at"] = prev.get("raised_at", payload["raised_at"])
    write_json(paths["alert_unpushed"], payload)
    return {"complete_unpushed": payload, "cleared": False}


def scan_and_alert(*, home: Path, db: Path, live_root: Path, require_live: bool = False) -> dict:
    now = now_cn()
    paths = home_paths(home)
    planned = plan_coverage(
        markers=paths["markers"],
        db=db,
        live_root=live_root,
        today=now.date(),
        lookback=lookback_days(),
        require_live=require_live,
    )
    pending = load_json(paths["pending"], None)
    alert = update_complete_unpushed_alert(home, planned["coverage"], bool(pending), now)
    return {"coverage": planned["coverage"], "pending": bool(pending), **alert}


def _publish_work(work: Path, staging: Path) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    for name in STAGING_FILES:
        source = work / name
        if not source.is_file():
            raise RuntimeError(f"package missing {name}")
        os.replace(source, staging / name)


def prepare_unpushed(
    *,
    home: Path,
    db: Path,
    live_root: Path,
    require_live: bool = False,
    prepare_fn=None,
) -> dict:
    """有 pending 或拿不到锁时不改 staging。失败不写 pending。"""
    paths = home_paths(home)
    paths["markers"].mkdir(parents=True, exist_ok=True)
    lock = PrepareLock(paths["prepare_lock"])
    if not lock.acquire():
        return {"ok": True, "prepared": [], "skipped": [{"reason": "lock_held"}]}
    work = paths["staging"] / f".work-{os.getpid()}"
    try:
        if paths["pending"].is_file():
            return {
                "ok": True,
                "prepared": [],
                "skipped": [{"reason": "pending_exists"}],
                "pending": load_json(paths["pending"], None),
            }
        now = now_cn()
        try:
            planned = plan_coverage(
                markers=paths["markers"],
                db=db,
                live_root=live_root,
                today=now.date(),
                lookback=lookback_days(),
                require_live=require_live,
            )
        except Exception as exc:
            return {"ok": False, "prepared": False, "retryable": True, "error": str(exc)}
        coverage = planned["coverage"]
        claimed = coverage.get("claimed_jc_days") or []
        update_complete_unpushed_alert(home, coverage, False, now)
        if not claimed:
            return {"ok": True, "prepared": [], "skipped": [{"reason": "nothing_to_claim"}], "coverage": coverage}
        if paths["pending"].is_file():
            return {
                "ok": True,
                "prepared": [],
                "skipped": [{"reason": "pending_exists"}],
                "pending": load_json(paths["pending"], None),
            }
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)
        jc_day = claimed[-1]
        builder = prepare_fn or (lambda day, cov, out_dir: build_package(db, out_dir, day, cov))
        try:
            meta = builder(jc_day, coverage, work)
        except Exception as exc:
            shutil.rmtree(work, ignore_errors=True)
            return {"ok": False, "prepared": False, "retryable": True, "error": str(exc), "coverage": coverage}
        if paths["pending"].is_file():
            shutil.rmtree(work, ignore_errors=True)
            return {
                "ok": True,
                "prepared": [],
                "skipped": [{"reason": "pending_exists"}],
                "pending": load_json(paths["pending"], None),
            }
        try:
            _publish_work(work, paths["staging"])
        except Exception as exc:
            return {"ok": False, "prepared": False, "retryable": True, "error": str(exc), "coverage": coverage}
        finally:
            shutil.rmtree(work, ignore_errors=True)
        pending = {
            "jc_day": jc_day,
            "ready_at": now.isoformat(),
            "staging_dir": str(paths["staging"]),
            "gz": str(paths["staging"] / "app_latest.db.gz"),
            "db_sha256": meta["db_sha256"],
            "gz_sha256": meta["gz_sha256"],
            "completeness": coverage["claimed_evidence"][-1],
            "coverage": coverage,
            "drop_hint": {
                "ypc": os.environ.get("AUTO_CN_PUSH_YPC_DROP", r"C:\Users\Administrator\football-cn-auto-push"),
                "msi": os.environ.get("AUTO_CN_PUSH_MSI_DROP", r"C:\Users\win10\football-cn-auto-push"),
                "next": "split_staging_chunks.py → CopyFromBox chunks → install_package_atomic.ps1 → wait LAST_PUSHED → --mark-pushed",
            },
            "LIVE_CAPTURE": "must stay off on CN",
        }
        write_json(paths["pending"], pending)
        write_json(paths["markers"] / f"pending_{jc_day}.json", pending)
        update_complete_unpushed_alert(home, coverage, True, now)
        _stage_mark(
            "cn.prepare",
            status="ok",
            summary={
                "jc_day": jc_day,
                "claimed_jc_days": claimed,
                "db_sha256_prefix": (pending.get("db_sha256") or "")[:16],
                "gz_sha256_prefix": (pending.get("gz_sha256") or "")[:16],
            },
            refs={"pending": str(paths["pending"])},
        )
        return {"ok": True, "prepared": [pending], "skipped": [], "coverage": coverage}
    finally:
        lock.release()


def _marker_matches(path: Path, db_sha: str, gz_sha: str) -> bool:
    if not path.is_file():
        return False
    payload = load_json(path, {}) or {}
    return norm_hash(payload.get("db_sha256")) == db_sha and norm_hash(payload.get("gz_sha256")) == gz_sha


def mark_confirmed(
    *,
    home: Path,
    jc_day: str,
    db: Path,
    live_root: Path,
    receipts: list[dict],
    ready: dict | None = None,
) -> dict:
    """哈希与 pending 不一致时不写已推标记。已标记且哈希相同则幂等返回。"""
    paths = home_paths(home)
    lock = PrepareLock(paths["prepare_lock"])
    if not lock.acquire():
        return {"ok": False, "reason": "lock_held", "marked": []}
    try:
        pending = load_json(paths["pending"], None)
        package = load_json(paths["staging"] / "meta.json", None)
        if pending and package:
            decision = delivery_decision(pending, ready, receipts, package)
        elif pending:
            decision = delivery_decision(pending, ready, receipts, pending)
        else:
            decision = {
                "allow_mark": False,
                "push_again": False,
                "needs_push": False,
                "matched_receipt": None,
                "stale_receipts": [],
                "reason": "no_pending",
            }
        claimed = []
        if pending and (pending.get("coverage") or {}).get("claimed_jc_days"):
            claimed = list(pending["coverage"]["claimed_jc_days"])
        elif jc_day:
            existing = load_json(marker_path(paths["markers"], jc_day), None)
            if existing and existing.get("claimed_jc_days"):
                claimed = list(existing["claimed_jc_days"])
        db_sha = norm_hash((pending or package or {}).get("db_sha256"))
        gz_sha = norm_hash((pending or package or {}).get("gz_sha256"))
        if not pending and claimed and receipts:
            matched = None
            for rec in receipts:
                if norm_hash(rec.get("db_sha256")) == db_sha and norm_hash(rec.get("gz_sha256")) == gz_sha:
                    matched = str(rec.get("source") or "receipt")
                    break
            if matched and all(_marker_matches(marker_path(paths["markers"], day), db_sha, gz_sha) for day in claimed):
                return {
                    "ok": True,
                    "already": True,
                    "marked": claimed,
                    "push_again": False,
                    "needs_push": False,
                    "matched_receipt": matched,
                    "reason": "already_marked",
                }
        if not decision.get("allow_mark"):
            # 没有回执匹配时，不允许凭最大已推日或未齐套日补写标记。
            if jc_day and not pending:
                row = check_day(jc_day, db, live_root, False)
                if not row.get("complete"):
                    return {
                        "ok": False,
                        "reason": "not_complete",
                        "marked": [],
                        "push_again": False,
                        "jc_day": jc_day,
                    }
            return {
                "ok": False,
                "reason": decision.get("reason") or "hash_mismatch",
                "marked": [],
                "push_again": False,
                "needs_push": bool(decision.get("needs_push")),
                "stale_receipts": decision.get("stale_receipts") or [],
            }
        if jc_day and claimed and jc_day not in claimed:
            return {"ok": False, "reason": "day_not_in_coverage", "marked": [], "push_again": False}
        if not claimed:
            row = check_day(jc_day, db, live_root, False)
            if not row.get("complete"):
                return {"ok": False, "reason": "not_complete", "marked": [], "push_again": False}
            claimed = [jc_day]
        if pending and (not package or norm_hash(package.get("db_sha256")) != db_sha or norm_hash(package.get("gz_sha256")) != gz_sha):
            return {"ok": False, "reason": "package_hash_mismatch", "marked": [], "push_again": False}
        for day in claimed:
            path = marker_path(paths["markers"], day)
            if path.is_file() and not _marker_matches(path, db_sha, gz_sha):
                return {
                    "ok": False,
                    "reason": "marker_hash_conflict",
                    "marked": [],
                    "push_again": False,
                }
        written = []
        now = now_cn()
        for day in claimed:
            path = marker_path(paths["markers"], day)
            if path.is_file() and not _marker_matches(path, db_sha, gz_sha):
                return {
                    "ok": False,
                    "reason": "marker_hash_conflict",
                    "marked": written,
                    "push_again": False,
                }
            if _marker_matches(path, db_sha, gz_sha):
                written.append(day)
                continue
            payload = {
                "jc_day": day,
                "pushed_at": now.isoformat(),
                "package_jc_day": (pending or {}).get("jc_day") or claimed[-1],
                "claimed_jc_days": claimed,
                "db_sha256": db_sha,
                "gz_sha256": gz_sha,
                "receipt_source": decision.get("matched_receipt"),
                "coverage": (pending or {}).get("coverage"),
                "note": "idempotent marker — will not auto-prepare again for this jc_day",
            }
            write_json(path, payload)
            written.append(day)
        if paths["pending"].is_file() and (paths["pending"] and (not claimed or (pending or {}).get("jc_day") in claimed or jc_day in claimed)):
            paths["pending"].unlink(missing_ok=True)
        _stage_mark(
            "cn.push",
            status="ok",
            summary={"jc_day": claimed[-1], "claimed_jc_days": claimed, "pushed_at": now.isoformat()},
            refs={"pushed_marker": str(marker_path(paths["markers"], claimed[-1]))},
        )
        return {
            "ok": True,
            "already": False,
            "marked": written,
            "push_again": False,
            "needs_push": False,
            "matched_receipt": decision.get("matched_receipt"),
            "stale_receipts": decision.get("stale_receipts") or [],
            "reason": "marked",
        }
    finally:
        lock.release()


def check_alerts(home: Path, db: Path, live_root: Path, timeout_hours: float) -> dict:
    paths = home_paths(home)
    now = now_cn()
    pend = load_json(paths["pending"], None)
    result = {"pending": pend, "alert": None, "cleared": False}
    if not pend:
        if paths["alert_pending"].is_file():
            paths["alert_pending"].unlink(missing_ok=True)
            result["cleared"] = True
    else:
        age = pending_age_hours(pend, now)
        result["age_hours"] = age
        if age is not None and age >= timeout_hours:
            payload = {
                "kind": "pending_timeout",
                "jc_day": pend.get("jc_day"),
                "ready_at": pend.get("ready_at"),
                "age_hours": round(age, 2),
                "timeout_hours": timeout_hours,
                "db_sha256": pend.get("db_sha256"),
                "gz_sha256": pend.get("gz_sha256"),
                "raised_at": now.isoformat(),
                "note": "pending package not mark-pushed in time — deliver atomically or investigate",
            }
            prev = load_json(paths["alert_pending"], {}) or {}
            same = prev and prev.get("jc_day") == payload["jc_day"] and prev.get("gz_sha256") == payload["gz_sha256"]
            if same:
                payload["raised_at"] = prev.get("raised_at", payload["raised_at"])
            write_json(paths["alert_pending"], payload)
            result["alert"] = payload
            ack = load_json(paths["alert_ack"], {}) or {}
            if ack and ack.get("gz_sha256") == payload["gz_sha256"]:
                result["acked"] = True
        elif paths["alert_pending"].is_file():
            paths["alert_pending"].unlink(missing_ok=True)
            result["cleared"] = True
    try:
        planned = plan_coverage(
            markers=paths["markers"],
            db=db,
            live_root=live_root,
            today=now.date(),
            lookback=lookback_days(),
        )
    except Exception as exc:
        result["stale_error"] = str(exc)
        return result
    days = _past_incomplete(planned["reports"], now.date(), set(planned["pushed"]))
    if not days:
        if paths["alert_stale"].is_file():
            paths["alert_stale"].unlink(missing_ok=True)
            result["stale_cleared"] = True
        result["stale_incomplete"] = None
    else:
        fingerprint = "|".join(f"{item['jc_day']}:{','.join(item['missing_match_uids'])}" for item in days)
        payload = {
            "kind": "stale_incomplete",
            "days": days,
            "fingerprint": fingerprint,
            "raised_at": now.isoformat(),
            "note": "a past jingcai day is still missing pinnacle rule close, so those days cannot be marked pushed",
        }
        prev = load_json(paths["alert_stale"], {}) or {}
        if prev.get("fingerprint") == fingerprint:
            payload["raised_at"] = prev.get("raised_at", payload["raised_at"])
        write_json(paths["alert_stale"], payload)
        result["stale_incomplete"] = payload
        ack = load_json(paths["alert_stale_ack"], {}) or {}
        if ack.get("fingerprint") == fingerprint:
            result["stale_acked"] = True
    extra = update_complete_unpushed_alert(home, planned["coverage"], bool(pend), now)
    result["complete_unpushed"] = extra.get("complete_unpushed")
    result["coverage"] = {
        "boundary": planned["coverage"].get("boundary"),
        "claimed_jc_days": planned["coverage"].get("claimed_jc_days"),
        "blocking_gap": planned["coverage"].get("blocking_gap"),
    }
    return result


def _load_receipts(paths: list[Path]) -> list[dict]:
    receipts = []
    for path in paths:
        payload = load_json(path, {}) or {}
        if "source" not in payload:
            payload["source"] = path.stem
        receipts.append(payload)
    return receipts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--mark-pushed", metavar="DAY")
    parser.add_argument("--date")
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--force", action="store_true", help="保留兼容；有 pending 时仍不覆盖")
    parser.add_argument("--check-alerts", action="store_true")
    parser.add_argument("--scan-unpushed", action="store_true")
    parser.add_argument("--check-delivery", action="store_true")
    parser.add_argument("--pending-timeout-hours", type=float, default=PENDING_TIMEOUT_HOURS)
    parser.add_argument("--ack-pending-alert", metavar="GZ_SHA")
    parser.add_argument("--ack-stale-incomplete", metavar="FINGERPRINT")
    parser.add_argument("--receipt", action="append", default=[], type=Path)
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--live-root", type=Path, default=DEFAULT_LIVE)
    parser.add_argument("--home", type=Path, default=OPS)
    args = parser.parse_args(argv)
    home = args.home
    paths = home_paths(home)
    paths["markers"].mkdir(parents=True, exist_ok=True)

    if args.ack_pending_alert:
        write_json(paths["alert_ack"], {"gz_sha256": args.ack_pending_alert, "acked_at": now_cn().isoformat()})
        print(json.dumps({"acked": args.ack_pending_alert}, ensure_ascii=False, indent=2))
        return 0
    if args.ack_stale_incomplete:
        write_json(
            paths["alert_stale_ack"],
            {"fingerprint": args.ack_stale_incomplete, "acked_at": now_cn().isoformat()},
        )
        print(json.dumps({"acked_stale_incomplete": args.ack_stale_incomplete}, ensure_ascii=False, indent=2))
        return 0
    if args.check_alerts:
        print(json.dumps(check_alerts(home, args.db, args.live_root, args.pending_timeout_hours), ensure_ascii=False, indent=2))
        return 0
    if args.scan_unpushed:
        try:
            print(json.dumps(scan_and_alert(home=home, db=args.db, live_root=args.live_root, require_live=args.require_live), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(json.dumps({"ok": False, "retryable": True, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        return 0
    if args.status:
        pushed = sorted(paths["markers"].glob("pushed_*.json")) if paths["markers"].is_dir() else []
        print(json.dumps({
            "pending": load_json(paths["pending"]),
            "staging_meta": load_json(paths["staging"] / "meta.json"),
            "pushed_markers": [p.name for p in pushed],
            "boundary": contiguous_boundary(pushed_days(paths["markers"])),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.check_delivery or (args.mark_pushed and args.receipt):
        receipts = _load_receipts(args.receipt)
        ready = load_json(args.ready, None) if args.ready else None
        if args.check_delivery and not args.mark_pushed:
            pending = load_json(paths["pending"], None)
            package = load_json(paths["staging"] / "meta.json", None)
            decision = delivery_decision(pending, ready, receipts, package)
            print(json.dumps(decision, ensure_ascii=False, indent=2))
            return 0 if decision.get("allow_mark") or decision.get("allow_push") or decision.get("reason") == "receipt_match" else 2
        result = mark_confirmed(
            home=home,
            jc_day=args.mark_pushed,
            db=args.db,
            live_root=args.live_root,
            receipts=receipts,
            ready=ready,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2
    if args.mark_pushed:
        result = mark_confirmed(
            home=home,
            jc_day=args.mark_pushed,
            db=args.db,
            live_root=args.live_root,
            receipts=[],
            ready=None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2
    if args.check and not args.prepare:
        if args.date:
            print(json.dumps({"results": [check_day(args.date, args.db, args.live_root, args.require_live)]}, ensure_ascii=False, indent=2))
            return 0
        planned = plan_coverage(
            markers=paths["markers"],
            db=args.db,
            live_root=args.live_root,
            today=now_cn().date(),
            lookback=lookback_days(),
            require_live=args.require_live,
        )
        print(json.dumps({"results": planned["reports"], "coverage": planned["coverage"]}, ensure_ascii=False, indent=2))
        return 0
    if not args.prepare:
        parser.error("use --check, --prepare, --status, --check-alerts, --scan-unpushed, or --mark-pushed")
    if args.force:
        # 兼容旧参数。pending 与在途 staging 仍然保留。
        pass
    result = prepare_unpushed(home=home, db=args.db, live_root=args.live_root, require_live=args.require_live)
    print(json.dumps({"prepared": result.get("prepared") or [], "skipped": result.get("skipped") or [], "ok": result.get("ok", False), "error": result.get("error"), "coverage": result.get("coverage")}, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
