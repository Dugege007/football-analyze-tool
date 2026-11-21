#!/usr/bin/env python3
"""独立于打包循环的看门狗。

循环活着与否以独占锁为准，不以 pid 文件的 kill -0 为准。
锁已释放才拉起；锁仍占用时只区分心跳，不结束进程、不拉第二份。
无法确认持有者是打包循环时只告警。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
PROD_HOME = Path("/workspace/football-analyze-tool-ops/auto-cn-push-after-close-20261020")
LOOP_MARK = "ensure_auto_cn_push_loop.sh"
PREPARE_MARKS = ("run_auto_cn_push.py", "prepare_formal_package.py")
LONG_PHASES = {"prepare", "check-alerts", "package"}
DEFAULT_STALE_SEC = 900
DEFAULT_LONG_OP_SEC = 3600
STARTUP_GRACE_SEC = 20


def production_guarded(home: Path) -> bool:
    flag = os.environ.get("AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL") == "1"
    try:
        same = home.resolve() == PROD_HOME.resolve()
    except OSError:
        same = str(home) == str(PROD_HOME)
    return same and not flag


def proc_starttime(pid: int, proc_root: Path) -> str | None:
    path = proc_root / str(pid) / "stat"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    cut = text.rfind(")")
    if cut < 0:
        return None
    fields = text[cut + 2 :].split()
    # stat 第 22 项 starttime；去掉 pid 与 comm 后从 state 起算，下标 19。
    if len(fields) < 20:
        return None
    return fields[19]


def proc_cmdline(pid: int, proc_root: Path) -> str | None:
    path = proc_root / str(pid) / "cmdline"
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if not raw:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def proc_children(pid: int, proc_root: Path) -> list[int]:
    path = proc_root / str(pid) / "task" / str(pid) / "children"
    if not path.is_file():
        return []
    out: list[int] = []
    for token in path.read_text(encoding="utf-8", errors="replace").split():
        if token.isdigit():
            out.append(int(token))
    return out


def dev_inode_keys(st: os.stat_result) -> set[str]:
    major = os.major(st.st_dev)
    minor = os.minor(st.st_dev)
    inode = st.st_ino
    return {
        f"{major:02x}:{minor:02x}:{inode}",
        f"{major:x}:{minor:x}:{inode}",
    }


def find_lock_holders(lock_path: Path, locks_text: str) -> list[int]:
    try:
        keys = dev_inode_keys(lock_path.stat())
    except OSError:
        return []
    holders: list[int] = []
    for line in locks_text.splitlines():
        parts = line.split()
        # 1: FLOCK ADVISORY WRITE <pid> <major:minor:inode> <start> <end>
        if len(parts) < 6 or not parts[4].isdigit():
            continue
        if parts[5] in keys:
            holders.append(int(parts[4]))
    return holders


def lock_is_free(lock_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _pid_alive(pid: int | None, proc_root: Path) -> bool:
    if not pid or pid <= 0:
        return False
    return (proc_root / str(pid) / "stat").is_file()


def _walk_prepare(pid: int, proc_root: Path, depth: int = 3) -> bool:
    if depth < 0:
        return False
    for child in proc_children(pid, proc_root):
        cmdline = proc_cmdline(child, proc_root) or ""
        if any(mark in cmdline for mark in PREPARE_MARKS):
            return True
        if _walk_prepare(child, proc_root, depth - 1):
            return True
    return False


def is_loop_cmdline(cmdline: str | None) -> bool | None:
    if cmdline is None:
        return None
    return LOOP_MARK in cmdline


def observe(home: Path, proc_root: Path = Path("/proc")) -> dict:
    logs = home / "logs"
    lock_path = logs / "ensure_auto_cn_push.lock"
    pid_text = ""
    pid_path = logs / "ensure_auto_cn_push.pid"
    if pid_path.is_file():
        raw_pid = pid_path.read_text(encoding="utf-8").strip()
        pid_text = raw_pid.split()[0] if raw_pid else ""
    pidfile_pid = int(pid_text) if pid_text.isdigit() else None
    identity = _read_json(logs / "loop_identity.json") or {}
    heartbeat = _read_json(logs / "heartbeat.json")
    free = lock_is_free(lock_path)
    holders: list[int] = []
    if not free:
        locks_path = proc_root / "locks"
        locks_text = ""
        if locks_path.is_file():
            locks_text = locks_path.read_text(encoding="utf-8", errors="replace")
        holders = find_lock_holders(lock_path, locks_text)
    holder_pid = holders[0] if len(holders) == 1 else None
    holder_cmd = proc_cmdline(holder_pid, proc_root) if holder_pid else None
    if len(holders) > 1:
        holder_state: bool | None = False
    else:
        holder_state = is_loop_cmdline(holder_cmd) if holder_pid else None
        if holder_pid is None and not free:
            # 锁占用但没解析出持有者：若心跳里的 pid 命令行是循环，仍可继续看心跳。
            hb_pid = int(heartbeat["pid"]) if heartbeat and str(heartbeat.get("pid") or "").isdigit() else None
            if hb_pid and is_loop_cmdline(proc_cmdline(hb_pid, proc_root)) is True:
                holder_state = True
                holder_pid = hb_pid
            else:
                holder_state = None
    child_alive = False
    if holder_pid and holder_state is True:
        child_alive = _walk_prepare(holder_pid, proc_root)
    recorded = identity.get("starttime") or (heartbeat or {}).get("starttime")
    return {
        "lock_held": not free,
        "holder_pid": holder_pid,
        "holder_is_loop": holder_state,
        "pidfile_pid": pidfile_pid,
        "pidfile_starttime": None if recorded is None else str(recorded),
        "pid_alive": _pid_alive(pidfile_pid, proc_root),
        "pid_starttime": proc_starttime(pidfile_pid, proc_root) if pidfile_pid else None,
        "heartbeat": heartbeat,
        "child_prepare_alive": child_alive,
        "now_epoch": time.time(),
    }


def assess(
    obs: dict,
    *,
    stale_sec: int = DEFAULT_STALE_SEC,
    long_op_max_sec: int = DEFAULT_LONG_OP_SEC,
    startup_grace_sec: int = STARTUP_GRACE_SEC,
) -> dict:
    decision = {
        "action": "ok",
        "reason": "",
        "exit_code": 0,
        "kill": False,
        "pid_reuse": False,
    }
    pid_reuse = False
    if obs.get("pid_alive") and obs.get("pidfile_starttime") and obs.get("pid_starttime"):
        if str(obs["pidfile_starttime"]) != str(obs["pid_starttime"]):
            pid_reuse = True
    decision["pid_reuse"] = pid_reuse
    heartbeat = obs.get("heartbeat") if isinstance(obs.get("heartbeat"), dict) else None
    age = None
    if heartbeat and heartbeat.get("beat_epoch") is not None:
        try:
            age = float(obs.get("now_epoch") or time.time()) - float(heartbeat["beat_epoch"])
        except (TypeError, ValueError):
            age = None

    if not obs.get("lock_held"):
        if (
            not pid_reuse
            and heartbeat
            and heartbeat.get("phase") == "starting"
            and age is not None
            and 0 <= age <= startup_grace_sec
        ):
            decision["reason"] = "startup_grace"
            return decision
        decision["action"] = "restart"
        decision["reason"] = "pid_reuse_loop_dead" if pid_reuse else "loop_dead"
        decision["kill"] = False
        return decision

    holder = obs.get("holder_is_loop")
    if holder is False:
        decision["action"] = "alert"
        decision["reason"] = "unsafe_lock_holder"
        decision["exit_code"] = 3
        decision["kill"] = False
        return decision

    phase = str((heartbeat or {}).get("phase") or "")
    child = bool(obs.get("child_prepare_alive"))
    if age is None:
        if child and phase in LONG_PHASES:
            decision["reason"] = "long_op_no_heartbeat"
            return decision
        decision["action"] = "alert"
        decision["reason"] = "unsafe_unknown_holder" if holder is None else "heartbeat_missing"
        decision["exit_code"] = 3 if holder is None else 2
        decision["kill"] = False
        return decision
    if age <= stale_sec:
        decision["reason"] = "healthy"
        return decision
    if phase in LONG_PHASES and child and age <= long_op_max_sec:
        decision["reason"] = "long_op"
        return decision
    if phase in LONG_PHASES and child and age > long_op_max_sec:
        decision["action"] = "alert"
        decision["reason"] = "long_op_exceeded"
        decision["exit_code"] = 2
        decision["kill"] = False
        return decision
    decision["action"] = "alert"
    decision["reason"] = "unsafe_unknown_holder" if holder is None else "heartbeat_stale"
    decision["exit_code"] = 3 if holder is None else 2
    decision["kill"] = False
    return decision


def _now_iso() -> str:
    return datetime.now(CN).isoformat(timespec="seconds")


def apply_decision(
    decision: dict,
    *,
    home: Path,
    starter,
) -> int:
    """执行看门狗决定。拒绝任何结束进程的请求。"""
    if decision.get("kill"):
        decision = {
            **decision,
            "action": "alert",
            "reason": "refusing_kill",
            "exit_code": 3,
            "kill": False,
        }
    logs = home / "logs"
    markers = home / "markers"
    logs.mkdir(parents=True, exist_ok=True)
    markers.mkdir(parents=True, exist_ok=True)
    alert_path = markers / "ALERT_LOOP_WATCHDOG.json"
    event_path = logs / "watchdog-events.log"
    record = {
        "at": _now_iso(),
        "action": decision.get("action"),
        "reason": decision.get("reason"),
        "kill": False,
        "pid_reuse": bool(decision.get("pid_reuse")),
    }
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if decision.get("action") == "restart":
        try:
            code = int(starter())
        except Exception as exc:  # 启动器本身失败，循环未被动过
            code = 1
            record["error"] = str(exc)
        if code != 0:
            payload = {
                "kind": "loop_watchdog",
                "reason": "start_failed",
                "action": "alert",
                "kill": False,
                "starter_exit": code,
                "raised_at": _now_iso(),
                "note": "打包循环未在运行，但未能安全拉起",
            }
            alert_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 1
        alert_path.unlink(missing_ok=True)
        return 0

    if decision.get("action") == "alert":
        payload = {
            "kind": "loop_watchdog",
            "reason": decision.get("reason"),
            "action": "alert",
            "kill": False,
            "pid_reuse": bool(decision.get("pid_reuse")),
            "raised_at": _now_iso(),
            "note": "锁仍被占用或持有者无法确认。不结束进程，不拉起第二份",
        }
        alert_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return int(decision.get("exit_code") or 2)

    alert_path.unlink(missing_ok=True)
    return 0


def _acquire_watchdog_lock(home: Path):
    path = home / "logs" / "watchdog.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def default_starter(script_dir: Path, home: Path) -> int:
    if production_guarded(home):
        print("REFUSING_PRODUCTION_START", file=sys.stderr)
        return 4
    script = script_dir / "start_ensure_auto_cn_push.sh"
    env = os.environ.copy()
    env["AUTO_CN_PUSH_HOME"] = str(home)
    completed = subprocess.run(["/bin/bash", str(script)], env=env, check=False)
    return int(completed.returncode)


def maybe_scan_alert(home: Path) -> None:
    db = os.environ.get("APP_DB_PATH", "").strip()
    if not db or not Path(db).is_file():
        return
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import run_auto_cn_push

    live = os.environ.get("ODDS_DATA_DIR", "").strip()
    live_root = Path(live) / "5dollar/live" if live else home / "live"
    run_auto_cn_push.scan_and_alert(home=home, db=Path(db), live_root=live_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--script-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--decide-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--starter", default="")
    parser.add_argument("--stale-sec", type=int, default=int(os.environ.get("AUTO_CN_PUSH_HEARTBEAT_STALE_SEC", DEFAULT_STALE_SEC)))
    parser.add_argument("--long-op-sec", type=int, default=int(os.environ.get("AUTO_CN_PUSH_LONG_OP_MAX_SEC", DEFAULT_LONG_OP_SEC)))
    parser.add_argument("--skip-scan", action="store_true")
    args = parser.parse_args(argv)
    home = args.home
    inspecting = args.decide_only or args.dry_run
    if production_guarded(home) and not inspecting:
        print("REFUSING_PRODUCTION_WATCHDOG", file=sys.stderr)
        return 4

    lock_fd = None
    if not inspecting:
        lock_fd = _acquire_watchdog_lock(home)
        if lock_fd is None:
            print(json.dumps({"action": "ok", "reason": "watchdog_already_running", "kill": False}, ensure_ascii=False))
            return 0
    try:
        obs = observe(home, args.proc_root)
        decision = assess(obs, stale_sec=args.stale_sec, long_op_max_sec=args.long_op_sec)
        if inspecting:
            print(json.dumps(decision, ensure_ascii=False))
            return 0
        if not args.skip_scan:
            try:
                maybe_scan_alert(home)
            except Exception as exc:
                print(f"[scan_warn] {exc}", file=sys.stderr)
        if args.starter:
            starter = lambda: subprocess.run([args.starter], check=False).returncode  # noqa: E731
        else:
            starter = lambda: default_starter(args.script_dir, home)  # noqa: E731
        code = apply_decision(decision, home=home, starter=starter)
        print(json.dumps(decision, ensure_ascii=False))
        return code
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
