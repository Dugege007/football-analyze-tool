#!/usr/bin/env python3
"""临时库、假钟和假推送上的保护测试。不连接生产，不推送。"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("STAGE_MARKS_DIR", tempfile.mkdtemp(prefix="cn-push-stage-"))
os.environ.setdefault("AUTO_CN_PUSH_NOW", "2026-11-21T12:00:00+08:00")

import protect  # noqa: E402
import run_auto_cn_push as runner  # noqa: E402
import watchdog  # noqa: E402
from check_close_complete import evaluate  # noqa: E402
from prepare_formal_package import build_package, gzip_commit_id, write_db_gzip  # noqa: E402

TODAY = date(2026, 11, 21)
PROD = "/workspace/football-analyze-tool-ops/auto-cn-push-after-close-20261020"


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_db(path: Path, days: dict[str, str]) -> None:
    """days 的值：complete / incomplete / none / macau / dropped。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE matches (id INTEGER PRIMARY KEY, match_uid TEXT)")
    con.execute(
        """CREATE TABLE odds_snapshot (
            id INTEGER PRIMARY KEY,
            match_id INTEGER,
            book TEXT, market TEXT, channel TEXT, point TEXT, line REAL
        )"""
    )
    match_id = 1
    for day, kind in days.items():
        if kind == "none":
            continue
        count = 2
        for index in range(count):
            uid = f"{day}|m{index}"
            con.execute("INSERT INTO matches (id, match_uid) VALUES (?, ?)", (match_id, uid))
            if kind == "complete" or (kind == "dropped" and index == 0):
                con.execute(
                    "INSERT INTO odds_snapshot (match_id, book, market, channel, point, line) VALUES (?, ?, ?, ?, ?, ?)",
                    (match_id, "pinnacle", "asian", "rule", "close", -0.5),
                )
            elif kind == "macau":
                con.execute(
                    "INSERT INTO odds_snapshot (match_id, book, market, channel, point, line) VALUES (?, ?, ?, ?, ?, ?)",
                    (match_id, "macau", "asian", "rule", "close", -0.25),
                )
            elif kind == "incomplete" and index == 0:
                con.execute(
                    "INSERT INTO odds_snapshot (match_id, book, market, channel, point, line) VALUES (?, ?, ?, ?, ?, ?)",
                    (match_id, "pinnacle", "asian", "rule", "close", None),
                )
            match_id += 1
    con.commit()
    con.close()


def historical_days() -> dict[str, str]:
    days = {"2026-11-11": "complete"}
    for day in range(12, 20):
        days[f"2026-11-{day:02d}"] = "complete"
    days["2026-11-20"] = "incomplete"
    days["2026-11-21"] = "incomplete"
    days["2026-11-22"] = "incomplete"
    return days


def seed_push_base(home: Path) -> dict:
    db_sha = "ab" * 32
    gz_sha = "cd" * 32
    jc_day = "2026-11-11"
    record = {
        "commit_id": "baseline-" + hashlib.sha256(f"msi\n{jc_day}\n{db_sha}\n{gz_sha}".encode()).hexdigest(),
        "kind": "baseline",
        "source": "msi_receipt",
        "receipt_source": "msi",
        "jc_day": jc_day,
        "db_sha256": db_sha,
        "gz_sha256": gz_sha,
        "snapshot_sha256": db_sha,
        "remote_db_sha256": db_sha,
        "initialized_from": "maintenance_baseline",
        "activated": False,
    }
    result = runner.import_push_base(home, record)
    if not result.get("ok"):
        raise RuntimeError(result)
    return result


def kill_tree(pid: int) -> None:
    children = Path(f"/proc/{pid}/task/{pid}/children")
    try:
        text = children.read_text(encoding="utf-8", errors="replace") if children.is_file() else ""
    except OSError:
        text = ""
    for token in text.split():
        if token.isdigit():
            kill_tree(int(token))
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def pids_with(fragment: str) -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        text = raw.replace(b"\0", b" ").decode("utf-8", "replace")
        if fragment in text:
            found.append(int(entry.name))
    return found


def wait_until(predicate, timeout: float = 8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("condition not met")


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cn-push-"))
        self.home = self.tmp / "home"
        self.live = self.tmp / "live"
        self.db = self.tmp / "app.db"
        self.home.mkdir()
        (self.home / "markers").mkdir()
        os.environ["AUTO_CN_PUSH_NOW"] = "2026-11-21T12:00:00+08:00"
        os.environ["STAGE_MARKS_DIR"] = str(self.tmp / "stage")
        seed_push_base(self.home)
        seed_push_base(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rules_not_relaxed(self):
        make_db(self.db, {"2026-11-18": "complete", "2026-11-19": "macau", "2026-11-20": "incomplete"})
        complete = evaluate("2026-11-18", self.db, self.live, False)
        macau = evaluate("2026-11-19", self.db, self.live, False)
        missing = evaluate("2026-11-20", self.db, self.live, False)
        empty = evaluate("2026-11-09", self.db, self.live, False)
        self.assertTrue(complete["complete"])
        self.assertFalse(macau["complete"])
        self.assertEqual(macau["pin_close_ok"], 0)
        self.assertFalse(missing["complete"])
        self.assertEqual(missing["reason"], "db_incomplete")
        self.assertEqual(empty["reason"], "no_matches")
        plan = self.live / "plan"
        plan.mkdir(parents=True)
        write_json(plan / "2026-11-20.json", {"kickoff_log": [{"event": "dropped_from_csl", "match_uid": "2026-11-20|m1"}]})
        # 撤下的场没有平博临盘，留下的一场有临盘，整日仍可齐套。
        drop_db = self.tmp / "dropped.db"
        make_db(drop_db, {"2026-11-20": "dropped"})
        dropped = evaluate("2026-11-20", drop_db, self.live, False)
        self.assertTrue(dropped["complete"])
        self.assertEqual(dropped["excluded_dropped_from_csl"], ["2026-11-20|m1"])

    def test_boundary_does_not_use_max_pushed_day(self):
        pushed = {"2026-11-11", "2026-11-20"}
        self.assertEqual(protect.contiguous_boundary(pushed), "2026-11-11")
        days = protect.scan_days("2026-11-11", TODAY, 14)
        self.assertEqual(days[0], "2026-11-12")
        self.assertIn("2026-11-15", days)
        self.assertNotEqual(days[0], "2026-11-21")

    def test_historical_complete_recent_two_incomplete(self):
        make_db(self.db, historical_days())
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        calls = {"n": 0}

        def builder(day, coverage, out_dir):
            calls["n"] += 1
            return build_package(self.db, out_dir, day, coverage)

        before = self.db.read_bytes()
        result = runner.prepare_unpushed(
            home=self.home, db=self.db, live_root=self.live, prepare_fn=builder
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(calls["n"], 1)
        claimed = result["coverage"]["claimed_jc_days"]
        self.assertEqual(claimed[0], "2026-11-12")
        self.assertEqual(claimed[-1], "2026-11-19")
        self.assertNotIn("2026-11-20", claimed)
        self.assertNotIn("2026-11-21", claimed)
        self.assertNotIn("2026-11-22", claimed)
        pending = json.loads((self.home / "markers" / "pending_push.json").read_text(encoding="utf-8"))
        self.assertEqual(pending["jc_day"], "2026-11-19")
        self.assertEqual(pending["coverage"]["claimed_jc_days"], claimed)
        meta = json.loads((self.home / "staging" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["coverage"]["claimed_jc_days"], claimed)
        self.assertEqual(self.db.read_bytes(), before)
        raw = self.tmp / "snap.db"
        with gzip.open(self.home / "staging" / "app_latest.db.gz", "rb") as src, raw.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        con = sqlite3.connect(raw)
        future = con.execute("SELECT match_uid FROM matches WHERE match_uid LIKE '2026-11-22|%'").fetchall()
        con.close()
        self.assertEqual(len(future), 2)
        refused = runner.mark_confirmed(
            home=self.home, jc_day="2026-11-21", db=self.db, live_root=self.live, receipts=[]
        )
        self.assertFalse(refused["ok"])
        self.assertFalse((self.home / "markers" / "pushed_2026-11-21.json").exists())
        self.assertFalse((self.home / "markers" / "pushed_2026-11-22.json").exists())

    def test_gap_no_match_and_cross_day(self):
        days = {
            "2026-11-12": "complete",
            "2026-11-13": "none",
            "2026-11-14": "complete",
            "2026-11-15": "incomplete",
            "2026-11-16": "complete",
            "2026-11-17": "complete",
            "2026-11-18": "complete",
            "2026-11-19": "complete",
            "2026-11-21": "incomplete",
        }
        make_db(self.db, days)
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        write_json(self.home / "markers" / "pushed_2026-11-20.json", {"jc_day": "2026-11-20", "db_sha256": "old"})
        calls = {"n": 0}

        def builder(day, coverage, out_dir):
            calls["n"] += 1
            return build_package(self.db, out_dir, day, coverage)

        result = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live, prepare_fn=builder)
        coverage = result["coverage"]
        self.assertEqual(calls["n"], 1)
        self.assertEqual(coverage["boundary"], "2026-11-11")
        self.assertEqual(
            coverage["claimed_jc_days"],
            ["2026-11-12", "2026-11-14", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"],
        )
        self.assertIn("2026-11-13", coverage["no_matches"])
        self.assertEqual(coverage["gap_days"][0]["jc_day"], "2026-11-15")
        self.assertEqual(coverage["withheld_after_gap"], [])
        self.assertIn("2026-11-16", coverage["claimed_jc_days"])
        self.assertNotIn("2026-11-15", coverage["claimed_jc_days"])
        self.assertIn("2026-11-20", coverage["already_pushed_ignored"])
        self.assertEqual(coverage["boundary"], "2026-11-11")
        alerts = runner.check_alerts(self.home, self.db, self.live, 2)
        stale_days = [item["jc_day"] for item in alerts["stale_incomplete"]["days"]]
        self.assertIn("2026-11-15", stale_days)
        self.assertNotIn("2026-11-12", stale_days)
        self.assertNotIn("2026-11-13", stale_days)
        self.assertNotIn("2026-11-20", stale_days)

    def test_pending_not_overwritten(self):
        make_db(self.db, historical_days())
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        pending_path = self.home / "markers" / "pending_push.json"
        write_json(pending_path, {"jc_day": "2026-11-19", "db_sha256": "keepdb", "gz_sha256": "keepgz", "ready_at": "2026-11-21T08:00:00+08:00"})
        staging = self.home / "staging"
        staging.mkdir()
        (staging / "app_latest.db.gz").write_bytes(b"KEEP")
        original = pending_path.read_text(encoding="utf-8")
        result = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertEqual(result["skipped"][0]["reason"], "pending_exists")
        self.assertEqual((staging / "app_latest.db.gz").read_bytes(), b"KEEP")
        self.assertEqual(pending_path.read_text(encoding="utf-8"), original)

    def test_prepare_failure_retries(self):
        make_db(self.db, historical_days())
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        staging = self.home / "staging"
        staging.mkdir()
        (staging / "app_latest.db.gz").write_bytes(b"OLD")
        state = {"n": 0}

        def builder(day, coverage, out_dir):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("disk full")
            return build_package(self.db, out_dir, day, coverage)

        first = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live, prepare_fn=builder)
        self.assertFalse(first["ok"])
        self.assertTrue(first["retryable"])
        self.assertEqual((staging / "app_latest.db.gz").read_bytes(), b"OLD")
        self.assertFalse((self.home / "markers" / "pending_push.json").exists())
        second = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live, prepare_fn=builder)
        self.assertTrue(second["ok"], second)
        self.assertTrue((self.home / "markers" / "pending_push.json").is_file())
        self.assertNotEqual((staging / "app_latest.db.gz").read_bytes(), b"OLD")

    def test_lock_held_does_not_prepare(self):
        import fcntl

        make_db(self.db, historical_days())
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        lock_path = self.home / "logs" / "prepare.lock"
        lock_path.parent.mkdir(exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            result = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(result["skipped"][0]["reason"], "lock_held")
        self.assertFalse((self.home / "markers" / "pending_push.json").exists())

    def _prepared(self):
        make_db(self.db, historical_days())
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        result = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertTrue(result["ok"], result)
        pending = json.loads((self.home / "markers" / "pending_push.json").read_text(encoding="utf-8"))
        return pending

    def test_hash_mismatch_does_not_mark(self):
        pending = self._prepared()
        receipt = self.tmp / "msi.json"
        write_json(receipt, {"source": "msi", "db_sha256": "deadbeef", "gz_sha256": pending["gz_sha256"]})
        result = runner.mark_confirmed(
            home=self.home,
            jc_day=pending["jc_day"],
            db=self.db,
            live_root=self.live,
            receipts=[json.loads(receipt.read_text(encoding="utf-8"))],
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["push_again"])
        pushed = sorted(path.name for path in (self.home / "markers").glob("pushed_*.json"))
        self.assertEqual(pushed, ["pushed_2026-11-11.json"])
        self.assertTrue((self.home / "markers" / "pending_push.json").is_file())

    def test_msi_match_stale_ypc_does_not_repush(self):
        pending = self._prepared()
        first_bytes = (self.home / "staging" / "app_latest.db.gz").read_bytes()
        receipts = [
            {"source": "msi", "db_sha256": pending["db_sha256"], "gz_sha256": pending["gz_sha256"]},
            {"source": "ypc", "db_sha256": "old-db", "gz_sha256": "old-gz"},
        ]
        ready = {"db_sha256": "old-db", "gz_sha256": "old-gz", "jc_day": "2026-11-11"}
        decision = protect.delivery_decision(pending, ready, receipts, pending)
        self.assertTrue(decision["allow_mark"])
        self.assertFalse(decision["allow_push"])
        self.assertFalse(decision["push_again"])
        self.assertEqual(decision["matched_receipt"], "msi")
        self.assertEqual(decision["ready_ignored"], "old_ready")
        marked = runner.mark_confirmed(
            home=self.home,
            jc_day=pending["jc_day"],
            db=self.db,
            live_root=self.live,
            receipts=receipts,
            ready=ready,
        )
        self.assertTrue(marked["ok"], marked)
        self.assertFalse(marked["push_again"])
        self.assertFalse((self.home / "markers" / "pending_push.json").exists())
        for day in pending["coverage"]["claimed_jc_days"]:
            path = self.home / "markers" / f"pushed_{day}.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["db_sha256"], pending["db_sha256"])
            self.assertEqual(payload["gz_sha256"], pending["gz_sha256"])
        self.assertFalse((self.home / "markers" / "pushed_2026-11-20.json").exists())
        self.assertFalse((self.home / "markers" / "pushed_2026-11-21.json").exists())
        again = runner.mark_confirmed(
            home=self.home,
            jc_day=pending["jc_day"],
            db=self.db,
            live_root=self.live,
            receipts=receipts,
        )
        self.assertTrue(again["ok"], again)
        self.assertTrue(again["already"])
        self.assertFalse(again["push_again"])
        self.assertEqual((self.home / "staging" / "app_latest.db.gz").read_bytes(), first_bytes)

    def test_old_ready_and_old_package_refused(self):
        pending = self._prepared()
        old_ready = {"db_sha256": "old-db", "gz_sha256": "old-gz"}
        decision = protect.delivery_decision(pending, old_ready, [], pending)
        self.assertFalse(decision["allow_push"])
        self.assertFalse(decision["allow_mark"])
        self.assertFalse(decision["push_again"])
        self.assertEqual(decision["reason"], "old_ready")
        old_package = {"db_sha256": "old-db", "gz_sha256": "old-gz"}
        mismatch = protect.delivery_decision(pending, None, [], old_package)
        self.assertEqual(mismatch["reason"], "package_hash_mismatch")
        self.assertFalse(mismatch["allow_push"])
        marked = runner.mark_confirmed(
            home=self.home,
            jc_day=pending["jc_day"],
            db=self.db,
            live_root=self.live,
            receipts=[],
            ready=old_ready,
        )
        self.assertFalse(marked["ok"])
        self.assertEqual(list((self.home / "markers").glob("pushed_*.json")), [self.home / "markers" / "pushed_2026-11-11.json"])

    def test_split_refuses_old_package(self):
        pending = self._prepared()
        staging = self.home / "staging"
        meta = json.loads((staging / "meta.json").read_text(encoding="utf-8"))
        meta["gz_sha256"] = "not-the-pending"
        (staging / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        env = os.environ.copy()
        env["AUTO_CN_PUSH_HOME"] = str(self.home)
        completed = subprocess.run(
            ["python3", str(ROOT / "split_staging_chunks.py"), "--staging", str(staging), "--out-dir", str(self.tmp / "chunks")],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("old package", completed.stderr + completed.stdout)
        self.assertFalse((self.tmp / "chunks").exists())


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cn-watch-"))
        self.home = self.tmp / "home"
        (self.home / "logs").mkdir(parents=True)
        (self.home / "markers").mkdir()
        self.db = self.tmp / "loop.db"
        self.db.write_bytes(b"x")
        self.odds = self.tmp / "odds"
        self.odds.mkdir()
        self.env_file = self.tmp / "push.env"
        self.env_file.write_text(
            f'APP_DB_PATH="{self.db}"\nODDS_DATA_DIR="{self.odds}"\n',
            encoding="utf-8",
        )
        self._old_env = os.environ.get("AUTO_CN_PUSH_ENV")
        os.environ["AUTO_CN_PUSH_ENV"] = str(self.env_file)
        self.procs: list[subprocess.Popen] = []
        self.extra_pids: list[int] = []

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
        for pid in self.extra_pids:
            kill_tree(pid)
        for proc in self.procs:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        if self._old_env is None:
            os.environ.pop("AUTO_CN_PUSH_ENV", None)
        else:
            os.environ["AUTO_CN_PUSH_ENV"] = self._old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_assess_matrix(self):
        now = 1_000_000.0
        dead = watchdog.assess({"lock_held": False, "pid_alive": False, "heartbeat": None, "now_epoch": now})
        self.assertEqual(dead["action"], "restart")
        self.assertFalse(dead["kill"])
        reused = watchdog.assess({
            "lock_held": False,
            "pid_alive": True,
            "pidfile_starttime": "1",
            "pid_starttime": "2",
            "heartbeat": None,
            "now_epoch": now,
        })
        self.assertEqual(reused["reason"], "pid_reuse_loop_dead")
        self.assertFalse(reused["kill"])
        healthy = watchdog.assess({
            "lock_held": True,
            "holder_is_loop": True,
            "heartbeat": {"beat_epoch": now - 10, "phase": "sleep"},
            "now_epoch": now,
            "child_prepare_alive": False,
        }, stale_sec=900, long_op_max_sec=3600)
        self.assertEqual(healthy["reason"], "healthy")
        long_op = watchdog.assess({
            "lock_held": True,
            "holder_is_loop": True,
            "heartbeat": {"beat_epoch": now - 1800, "phase": "prepare"},
            "now_epoch": now,
            "child_prepare_alive": True,
        }, stale_sec=900, long_op_max_sec=3600)
        self.assertEqual(long_op["reason"], "long_op")
        self.assertEqual(long_op["action"], "ok")
        self.assertFalse(long_op["kill"])
        stale = watchdog.assess({
            "lock_held": True,
            "holder_is_loop": True,
            "heartbeat": {"beat_epoch": now - 2000, "phase": "sleep"},
            "now_epoch": now,
            "child_prepare_alive": False,
        }, stale_sec=900, long_op_max_sec=3600)
        self.assertEqual(stale["reason"], "heartbeat_stale")
        self.assertEqual(stale["action"], "alert")
        self.assertFalse(stale["kill"])
        unsafe = watchdog.assess({
            "lock_held": True,
            "holder_is_loop": False,
            "heartbeat": {"beat_epoch": now, "phase": "sleep"},
            "now_epoch": now,
        })
        self.assertEqual(unsafe["reason"], "unsafe_lock_holder")
        self.assertFalse(unsafe["kill"])
        forced = watchdog.apply_decision({"action": "restart", "kill": True, "reason": "nope"}, home=self.home, starter=lambda: 0)
        self.assertEqual(forced, 3)
        alert = json.loads((self.home / "markers" / "ALERT_LOOP_WATCHDOG.json").read_text(encoding="utf-8"))
        self.assertEqual(alert["reason"], "refusing_kill")
        self.assertFalse(alert["kill"])

    def test_pid_reuse_restarts_without_killing(self):
        me = os.getpid()
        write_json(self.home / "logs" / "loop_identity.json", {"pid": me, "starttime": "1"})
        (self.home / "logs" / "ensure_auto_cn_push.pid").write_text(f"{me}\n", encoding="ascii")
        note = self.tmp / "started.txt"
        starter = self.tmp / "starter.sh"
        starter.write_text(f"#!/bin/sh\necho started > {note}\n", encoding="utf-8")
        starter.chmod(0o755)
        completed = subprocess.run(
            ["python3", str(ROOT / "watchdog.py"), "--home", str(self.home), "--starter", str(starter), "--skip-scan"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue(note.is_file())
        os.kill(me, 0)
        self.assertIn("pid_reuse_loop_dead", completed.stdout)

    def test_duplicate_watchdog_does_not_start(self):
        import fcntl

        lock = self.home / "logs" / "watchdog.lock"
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            completed = subprocess.run(
                ["python3", str(ROOT / "watchdog.py"), "--home", str(self.home), "--skip-scan"],
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("watchdog_already_running", completed.stdout)
        self.assertFalse((self.home / "logs" / "ensure_auto_cn_push.pid").exists())

    def _fake_python(self, prepare_sleep: int) -> Path:
        path = self.tmp / "fake-python.sh"
        log = self.tmp / "fake.log"
        path.write_text(
            "#!/bin/bash\n"
            f"if [[ \"$1\" == *write_heartbeat.py ]]; then exec /usr/bin/python3 \"$@\"; fi\n"
            f"echo \"$*\" >> {log}\n"
            f"if [[ \"$*\" == *--prepare* ]]; then sleep {prepare_sleep}; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    def _start_loop(self, fake: Path, interval: int = 30) -> int:
        env = os.environ.copy()
        env["AUTO_CN_PUSH_HOME"] = str(self.home)
        env["AUTO_CN_PUSH_PYTHON"] = str(fake)
        env["AUTO_CN_PUSH_INTERVAL_SEC"] = str(interval)
        env["STAGE_MARKS_DIR"] = str(self.tmp / "stage")
        env["AUTO_CN_PUSH_ENV"] = str(self.env_file)
        env.pop("AUTO_CN_PUSH_ONCE", None)
        proc = subprocess.Popen(
            ["bash", str(ROOT / "ensure_auto_cn_push_loop.sh")],
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.procs.append(proc)
        self.extra_pids.append(proc.pid)
        return proc.pid

    def test_long_op_is_not_killed(self):
        self._start_loop(self._fake_python(20))
        def phase():
            path = self.home / "logs" / "heartbeat.json"
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if data.get("phase") == "prepare" else None
        beat = wait_until(phase)
        beat["beat_epoch"] = time.time() - 1800
        write_json(self.home / "logs" / "heartbeat.json", beat)
        completed = subprocess.run(
            ["python3", str(ROOT / "watchdog.py"), "--home", str(self.home), "--decide-only", "--skip-scan",
             "--stale-sec", "900", "--long-op-sec", "3600"],
            text=True, capture_output=True, check=False,
        )
        decision = json.loads(completed.stdout)
        self.assertEqual(decision["reason"], "long_op", completed.stderr)
        self.assertFalse(decision["kill"])
        pid = int((self.home / "logs" / "ensure_auto_cn_push.pid").read_text().strip())
        os.kill(pid, 0)

    def test_stale_heartbeat_alerts_without_second_start(self):
        self._start_loop(self._fake_python(0), interval=40)
        def sleeping():
            path = self.home / "logs" / "heartbeat.json"
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if data.get("phase") == "sleep" else None
        beat = wait_until(sleeping)
        beat["beat_epoch"] = time.time() - 2000
        write_json(self.home / "logs" / "heartbeat.json", beat)
        before = pids_with(str(ROOT / "ensure_auto_cn_push_loop.sh"))
        completed = subprocess.run(
            ["python3", str(ROOT / "watchdog.py"), "--home", str(self.home), "--skip-scan",
             "--stale-sec", "900", "--long-op-sec", "3600"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        decision = json.loads(completed.stdout)
        self.assertEqual(decision["reason"], "heartbeat_stale")
        self.assertFalse(decision["kill"])
        pid = int((self.home / "logs" / "ensure_auto_cn_push.pid").read_text().strip())
        os.kill(pid, 0)
        after = pids_with(str(ROOT / "ensure_auto_cn_push_loop.sh"))
        self.assertEqual(sorted(after), sorted(before))
        alert = json.loads((self.home / "markers" / "ALERT_LOOP_WATCHDOG.json").read_text(encoding="utf-8"))
        self.assertEqual(alert["reason"], "heartbeat_stale")

    def test_duplicate_start_and_foreign_lock(self):
        self._start_loop(self._fake_python(20))
        wait_until(lambda: (self.home / "logs" / "heartbeat.json").is_file())
        env = os.environ.copy()
        env["AUTO_CN_PUSH_HOME"] = str(self.home)
        env["AUTO_CN_PUSH_PYTHON"] = "/usr/bin/python3"
        second = subprocess.run(
            ["bash", str(ROOT / "start_ensure_auto_cn_push.sh")],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("already running", second.stdout)
        loops = [pid for pid in pids_with(str(ROOT / "ensure_auto_cn_push_loop.sh")) if pid != os.getpid()]
        self.assertEqual(len(loops), 1)
        foreign_home = self.tmp / "foreign"
        (foreign_home / "logs").mkdir(parents=True)
        (foreign_home / "markers").mkdir()
        lock = foreign_home / "logs" / "ensure_auto_cn_push.lock"
        holder = subprocess.Popen(
            ["flock", "-x", str(lock), "sleep", "20"],
            start_new_session=True,
        )
        self.procs.append(holder)
        time.sleep(0.3)
        viewed = subprocess.run(
            ["python3", str(ROOT / "watchdog.py"), "--home", str(foreign_home), "--decide-only", "--skip-scan"],
            text=True, capture_output=True, check=False,
        )
        decision = json.loads(viewed.stdout)
        self.assertEqual(decision["reason"], "unsafe_lock_holder")
        self.assertFalse(decision["kill"])
        os.kill(holder.pid, 0)

    def test_dead_loop_recovers_and_prepares_history(self):
        db = self.tmp / "app.db"
        live = self.tmp / "live"
        make_db(db, historical_days())
        before = db.read_bytes()
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        seed_push_base(self.home)
        (self.home / "logs" / "ensure_auto_cn_push.pid").write_text("999999\n", encoding="ascii")
        odds = self.tmp / "odds"
        odds.mkdir(exist_ok=True)
        self.env_file.write_text(
            f'APP_DB_PATH="{db}"\nODDS_DATA_DIR="{odds}"\n',
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update({
            "AUTO_CN_PUSH_HOME": str(self.home),
            "AUTO_CN_PUSH_ONCE": "1",
            "AUTO_CN_PUSH_NOW": "2026-11-21T12:00:00+08:00",
            "AUTO_CN_PUSH_ENV": str(self.env_file),
            "STAGE_MARKS_DIR": str(self.tmp / "stage"),
        })
        env.pop("APP_DB_PATH", None)
        completed = subprocess.run(
            ["python3", str(ROOT / "watchdog.py"), "--home", str(self.home), "--script-dir", str(ROOT)],
            env=env, text=True, capture_output=True, check=False, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("loop_dead", completed.stdout)
        pending_path = self.home / "markers" / "pending_push.json"
        wait_until(lambda: pending_path.is_file(), timeout=15)
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        self.assertEqual(pending["coverage"]["claimed_jc_days"][0], "2026-11-12")
        self.assertEqual(pending["coverage"]["claimed_jc_days"][-1], "2026-11-19")
        self.assertNotIn("2026-11-20", pending["coverage"]["claimed_jc_days"])
        self.assertNotIn("2026-11-22", pending["coverage"]["claimed_jc_days"])
        self.assertEqual(db.read_bytes(), before)
        self.assertTrue((self.home / "markers" / "ALERT_COMPLETE_UNPUSHED.json").exists() or pending_path.exists())
        os.kill(os.getpid(), 0)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cn-install-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.cron = self.tmp / "cron"
        self.db = self.tmp / "app.db"
        self.db.write_bytes(b"x")
        self.odds = self.tmp / "odds"
        self.odds.mkdir()
        self.env_file = self.tmp / "push.env"
        self.env_file.write_text(
            f'APP_DB_PATH="{self.db}"\nODDS_DATA_DIR="{self.odds}"\n',
            encoding="utf-8",
        )
        for name in (
            "install_watchdog.sh",
            "rollback_watchdog.sh",
            "verify_watchdog.sh",
            "ensure_auto_cn_push_watch.sh",
            "install_box_cron.sh",
        ):
            os.chmod(ROOT / name, 0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, home: str | None = None):
        env = os.environ.copy()
        env["AUTO_CN_PUSH_HOME"] = home or str(self.home)
        env["AUTO_CN_PUSH_CRON_FILE"] = str(self.cron)
        env["AUTO_CN_PUSH_ENV"] = str(self.env_file)
        env.pop("AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL", None)
        env.pop("CRONTAB_BIN", None)
        return env

    def test_install_is_idempotent_and_rolls_back(self):
        install = subprocess.run(["bash", str(ROOT / "install_watchdog.sh")], env=self.env(), text=True, capture_output=True, check=False)
        self.assertEqual(install.returncode, 0, install.stderr)
        subprocess.run(["bash", str(ROOT / "install_watchdog.sh")], env=self.env(), check=True)
        text = self.cron.read_text(encoding="utf-8")
        self.assertEqual(text.count("# fat-auto-cn-push-watchdog"), 1)
        self.assertNotIn("ALLOW_PRODUCTION", text)
        install_source = (ROOT / "install_watchdog.sh").read_text(encoding="utf-8")
        self.assertIn('PREFIX="AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL=1 "', install_source)
        verified = subprocess.run(["bash", str(ROOT / "verify_watchdog.sh")], env=self.env(), text=True, capture_output=True, check=False)
        self.assertEqual(verified.returncode, 2, verified.stdout + verified.stderr)
        self.assertIn("VERIFY_LOOP", verified.stdout)
        rolled = subprocess.run(["bash", str(ROOT / "rollback_watchdog.sh")], env=self.env(), text=True, capture_output=True, check=False)
        self.assertEqual(rolled.returncode, 0, rolled.stderr)
        self.assertNotIn("# fat-auto-cn-push-watchdog", self.cron.read_text(encoding="utf-8"))
        subprocess.run(["bash", str(ROOT / "rollback_watchdog.sh")], env=self.env(), check=True)
        again = subprocess.run(["bash", str(ROOT / "verify_watchdog.sh")], env=self.env(), text=True, capture_output=True, check=False)
        self.assertNotEqual(again.returncode, 0)

    def test_production_install_refused(self):
        completed = subprocess.run(
            ["bash", str(ROOT / "install_watchdog.sh")],
            env=self.env(PROD),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 4)
        self.assertIn("REFUSING_PRODUCTION_INSTALL", completed.stdout)
        self.assertFalse(Path(PROD).exists())
        legacy = subprocess.run(
            ["bash", str(ROOT / "install_box_cron.sh")],
            env=self.env(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(legacy.returncode, 4)
        self.assertIn("REFUSING_LEGACY_PREPARE_CRON", legacy.stdout)


def stage_drop(directory: Path, raw: bytes, mtime: int, jc_day: str = "2026-11-20", expected_commit_id: str | None = None) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    gz_path = directory / "app_latest.db.gz"
    write_db_gzip(raw, gz_path, expected_commit_id, mtime=mtime)
    blob = gz_path.read_bytes()
    db_sha = hashlib.sha256(raw).hexdigest()
    gz_sha = hashlib.sha256(blob).hexdigest()
    (directory / "app_latest.db.sha256").write_text(db_sha + "\n", encoding="ascii")
    (directory / "app_latest.db.gz.sha256").write_text(gz_sha + "\n", encoding="ascii")
    meta = {"jc_day": jc_day, "db_sha256": db_sha, "gz_sha256": gz_sha}
    if expected_commit_id:
        meta["expected_commit_id"] = expected_commit_id
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (directory / "app_latest.ready").write_text(json.dumps({
        "db_sha256": db_sha,
        "gz_sha256": gz_sha,
    }), encoding="utf-8")
    return gz_sha


def write_service_hook(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "python3 - <<'PY'\n"
        "import hashlib, json, os\n"
        "from pathlib import Path\n"
        "role = os.environ['FAT_CN_HOOK_ROLE']\n"
        "db = Path(os.environ['FAT_CN_DB'])\n"
        "mode = os.environ.get('FAT_CN_HOOK_MODE', 'match_file')\n"
        "served = Path(os.environ['FAT_CN_SERVED'])\n"
        "log = Path(os.environ['FAT_CN_HOOK_LOG'])\n"
        "actual = hashlib.sha256(db.read_bytes()).hexdigest() if db.is_file() else ''\n"
        "log.parent.mkdir(parents=True, exist_ok=True)\n"
        "with log.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(role + '\\n')\n"
        "if mode == 'old_connection' and role == 'activate':\n"
        "    previous = served.read_text(encoding='utf-8').strip() if served.is_file() else actual\n"
        "    print(json.dumps({'ok': True, 'db_sha256': previous, 'read_only': True, 'fingerprint_source': 'disk'}))\n"
        "elif mode == 'disk_only':\n"
        "    print(json.dumps({'ok': True, 'db_sha256': actual, 'read_only': True, 'fingerprint_source': 'disk'}))\n"
        "elif mode == 'rollback_lies' and role == 'activate':\n"
        "    previous = served.read_text(encoding='utf-8').strip() if served.is_file() else ''\n"
        "    print(json.dumps({'ok': True, 'db_sha256': previous, 'read_only': True, 'fingerprint_source': 'disk'}))\n"
        "elif mode == 'rollback_lies' and role == 'rollback':\n"
        "    print(json.dumps({'ok': True, 'db_sha256': '0' * 64, 'read_only': True, 'fingerprint_source': 'running_api'}))\n"
        "else:\n"
        "    served.parent.mkdir(parents=True, exist_ok=True)\n"
        "    served.write_text(actual + '\\n', encoding='utf-8')\n"
        "    print(json.dumps({'ok': True, 'db_sha256': actual, 'read_only': True, 'fingerprint_source': 'running_api'}))\n"
        "PY\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def local_ssh(tmp: Path) -> Path:
    ssh_bin = tmp / "bin"
    ssh_bin.mkdir(exist_ok=True)
    ssh = ssh_bin / "ssh"
    ssh.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  case \"$1\" in\n"
        "    -T|-t) shift ;;\n"
        "    -o|-i) shift 2 ;;\n"
        "    *) break ;;\n"
        "  esac\ndone\nshift\nexec bash -c \"$1\"\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)
    return ssh_bin


def service_env(tmp: Path, server: Path, mode: str = "match_file") -> dict:
    hook = tmp / "hook-dir" / "service.sh"
    write_service_hook(hook)
    env = os.environ.copy()
    env.update({
        "PATH": str(local_ssh(tmp)) + os.pathsep + env.get("PATH", ""),
        "FAT_CN_ROOT": str(server),
        "FAT_CN_LOCK": str(tmp / "remote.lock"),
        "FAT_CN_ACTIVATE_CMD": str(hook),
        "FAT_CN_ROLLBACK_CMD": str(hook),
        "FAT_CN_SERVED": str(tmp / "served.txt"),
        "FAT_CN_HOOK_LOG": str(tmp / "hook.log"),
        "FAT_CN_HOOK_MODE": mode,
    })
    return env


class ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cn-review-"))
        self.home = self.tmp / "home"
        self.live = self.tmp / "live"
        self.db = self.tmp / "app.db"
        (self.home / "markers").mkdir(parents=True)
        (self.home / "logs").mkdir()
        os.environ["AUTO_CN_PUSH_NOW"] = "2026-11-21T12:00:00+08:00"
        os.environ["STAGE_MARKS_DIR"] = str(self.tmp / "stage")
        seed_push_base(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_repack_same_db_keeps_gz(self):
        make_db(self.db, {"2026-11-18": "complete"})
        first = build_package(self.db, self.tmp / "a", "2026-11-18", None)
        second = build_package(self.db, self.tmp / "b", "2026-11-18", None)
        self.assertEqual(first["gz_sha256"], second["gz_sha256"])
        self.assertEqual(first["db_sha256"], second["db_sha256"])

    def test_same_db_different_gz_is_not_marked(self):
        make_db(self.db, historical_days())
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        prepared = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        pending = prepared["prepared"][0]
        receipt_path = self.tmp / "LAST_PUSHED.json"
        receipt = {"source": "msi", "jc_day": "2026-11-20", "db_sha256": pending["db_sha256"], "gz_sha256": "a" * 64}
        receipt_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(receipt).encode("utf-8"))
        original = receipt_path.read_bytes()
        with self.assertRaises(json.JSONDecodeError):
            json.loads(receipt_path.read_text(encoding="utf-8"))
        loaded = runner.load_json(receipt_path)
        refused = runner.mark_confirmed(
            home=self.home, jc_day=pending["jc_day"], db=self.db, live_root=self.live,
            receipts=[loaded],
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason"], "needs_gz_commit")
        self.assertFalse(refused["push_again"])
        self.assertTrue((self.home / "markers" / "pending_push.json").is_file())
        self.assertEqual(receipt_path.read_bytes(), original)
        self.assertFalse((self.home / "markers" / "pushed_2026-11-19.json").exists())
        self.assertTrue((self.home / "markers" / "pending_push.json").is_file())

    def test_clean_env_does_not_start_or_use_default_db(self):
        env = os.environ.copy()
        env.pop("AUTO_CN_PUSH_ENV", None)
        env.pop("APP_DB_PATH", None)
        note = self.tmp / "started"
        starter = self.tmp / "starter.sh"
        starter.write_text(f"#!/bin/sh\necho no > {note}\n", encoding="utf-8")
        starter.chmod(0o755)
        completed = subprocess.run(
            ["python3", str(ROOT / "watchdog.py"), "--home", str(self.home), "--starter", str(starter), "--skip-scan"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 5, completed.stdout + completed.stderr)
        self.assertIn("config_missing", completed.stdout)
        self.assertFalse(note.exists())
        alert = json.loads((self.home / "markers" / "ALERT_CONFIG.json").read_text(encoding="utf-8"))
        self.assertIn("默认库", alert["note"])

    def test_env_path_with_spaces(self):
        root = self.tmp / "dir with space"
        home = root / "ops home"
        db = root / "db dir" / "app.db"
        make_db(db, {})
        odds = root / "odds dir"
        odds.mkdir(parents=True)
        env_file = root / "env dir" / "push env"
        env_file.parent.mkdir(parents=True)
        env_file.write_text(f'APP_DB_PATH="{db}"\nODDS_DATA_DIR="{odds}"\n', encoding="utf-8")
        cron = root / "cron file"
        env = os.environ.copy()
        env.update({
            "AUTO_CN_PUSH_HOME": str(home),
            "AUTO_CN_PUSH_ENV": str(env_file),
            "AUTO_CN_PUSH_CRON_FILE": str(cron),
        })
        env.pop("CRONTAB_BIN", None)
        completed = subprocess.run(["bash", str(ROOT / "install_watchdog.sh")], env=env, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        text = cron.read_text(encoding="utf-8")
        self.assertIn(f"AUTO_CN_PUSH_ENV='{env_file}'", text)
        self.assertEqual(text.count("# fat-auto-cn-push-watchdog"), 1)

    def _fake_crontab(self, mode: str) -> tuple[Path, Path]:
        state = self.tmp / "cron-state"
        if mode == "jobs":
            state.write_text("0 1 * * * /usr/bin/keep-me\n", encoding="utf-8")
        path = self.tmp / "fake-crontab"
        path.write_text(
            "#!/bin/bash\n"
            f"STATE='{state}'\n"
            f"MODE='{mode}'\n"
            'if [[ "$1" == "-l" ]]; then\n'
            '  if [[ "$MODE" == "fail" ]]; then echo "crontab: permission denied" >&2; exit 1; fi\n'
            '  if [[ "$MODE" == "none" && ! -s "$STATE" ]]; then echo "no crontab for user" >&2; exit 1; fi\n'
            '  if [[ -f "$STATE" ]]; then cat "$STATE"; fi\n'
            "  exit 0\n"
            "fi\n"
            'cat > "$STATE"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path, state

    def _install_env(self, crontab: Path) -> dict:
        db = self.tmp / "install.db"
        db.write_bytes(b"x")
        odds = self.tmp / "install-odds"
        odds.mkdir(exist_ok=True)
        env_file = self.tmp / "install.env"
        env_file.write_text(f'APP_DB_PATH="{db}"\nODDS_DATA_DIR="{odds}"\n', encoding="utf-8")
        env = os.environ.copy()
        env.update({
            "AUTO_CN_PUSH_HOME": str(self.home),
            "AUTO_CN_PUSH_ENV": str(env_file),
            "CRONTAB_BIN": str(crontab),
        })
        env.pop("AUTO_CN_PUSH_CRON_FILE", None)
        env.pop("AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL", None)
        return env

    def test_crontab_read_failure_does_not_replace(self):
        binary, state = self._fake_crontab("fail")
        state.write_text("0 1 * * * /usr/bin/keep-me\n", encoding="utf-8")
        before = state.read_bytes()
        completed = subprocess.run(
            ["bash", str(ROOT / "install_watchdog.sh")],
            env=self._install_env(binary), text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 6, completed.stdout + completed.stderr)
        self.assertIn("CRONTAB_READ_FAILED", completed.stdout)
        self.assertEqual(state.read_bytes(), before)

    def test_crontab_preserves_existing_jobs_and_rollback_restores(self):
        binary, state = self._fake_crontab("jobs")
        completed = subprocess.run(
            ["bash", str(ROOT / "install_watchdog.sh")],
            env=self._install_env(binary), text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        installed = state.read_text(encoding="utf-8")
        self.assertIn("/usr/bin/keep-me", installed)
        self.assertEqual(installed.count("# fat-auto-cn-push-watchdog"), 1)
        rolled = subprocess.run(
            ["bash", str(ROOT / "rollback_watchdog.sh")],
            env=self._install_env(binary), text=True, capture_output=True, check=False,
        )
        self.assertEqual(rolled.returncode, 0, rolled.stdout + rolled.stderr)
        restored = state.read_text(encoding="utf-8")
        self.assertIn("/usr/bin/keep-me", restored)
        self.assertNotIn("# fat-auto-cn-push-watchdog", restored)
        note = json.loads((self.home / "logs" / "ROLLBACK_DONE.json").read_text(encoding="utf-8"))
        self.assertTrue(note["windows_scripts_untouched"])
        self.assertTrue(note["pending_kept"])

    def test_no_crontab_is_not_a_read_failure(self):
        binary, state = self._fake_crontab("none")
        completed = subprocess.run(
            ["bash", str(ROOT / "install_watchdog.sh")],
            env=self._install_env(binary), text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("# fat-auto-cn-push-watchdog", state.read_text(encoding="utf-8"))

    def test_truncated_records_are_not_coverage(self):
        staging = self.home / "staging"
        staging.mkdir()
        (staging / "app_latest.db.gz").write_bytes(b"KEEP")
        pending = self.home / "markers" / "pending_push.json"
        pending.write_text('{"jc_day": "2026-11-19", ', encoding="utf-8")
        partial = pending.read_bytes()
        result = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertEqual(result["error"], "pending_corrupt")
        self.assertEqual((staging / "app_latest.db.gz").read_bytes(), b"KEEP")
        self.assertFalse(pending.exists())
        quarantined = list((self.home / "markers" / "quarantine").iterdir())
        self.assertTrue(any(item.read_bytes() == partial for item in quarantined))
        pushed = self.home / "markers" / "pushed_2026-11-12.json"
        pushed.write_text('{"jc_day":', encoding="utf-8")
        fragment = pushed.read_bytes()
        self.assertNotIn("2026-11-12", runner.pushed_days(self.home / "markers", self.home))
        self.assertTrue(any(item.read_bytes() == fragment for item in (self.home / "markers" / "quarantine").iterdir()))
        good = self.home / "markers" / "kept.json"
        runner.write_json(good, {"ok": True})
        before = good.read_text(encoding="utf-8")
        with self.assertRaises(RuntimeError):
            runner.write_json(good, {"ok": False}, fault="before_replace")
        self.assertEqual(good.read_text(encoding="utf-8"), before)

    def test_no_match_is_checked_without_repeated_scan(self):
        make_db(self.db, {"2026-11-12": "complete", "2026-11-13": "none", "2026-11-21": "incomplete"})
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        calls = []

        def checker(day):
            calls.append(day)
            return evaluate(day, self.db, self.live, False)

        first = runner.plan_coverage(
            home=self.home, markers=self.home / "markers", db=self.db, live_root=self.live,
            today=TODAY, lookback=14, check_fn=checker,
        )
        self.assertIn("2026-11-13", calls)
        self.assertIn("2026-11-12", first["coverage"]["claimed_jc_days"])
        calls.clear()
        second = runner.plan_coverage(
            home=self.home, markers=self.home / "markers", db=self.db, live_root=self.live,
            today=TODAY, lookback=14, check_fn=checker,
        )
        self.assertIn("2026-11-13", calls)
        self.assertTrue((self.home / "markers" / "checked_no_matches_2026-11-13.json").is_file())
        self.assertFalse((self.home / "markers" / "pushed_2026-11-13.json").exists())
    def test_transaction_same_db_new_gz_and_stale_predecessor(self):
        make_db(self.db, historical_days())
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        probe = build_package(self.db, self.tmp / "probe", "2026-11-20", None)
        raw = gzip.decompress((self.tmp / "probe" / "app_latest.db.gz").read_bytes())
        self.assertEqual(gzip_commit_id((self.tmp / "probe" / "app_latest.db.gz").read_bytes()), "")
        old_file = io.BytesIO()
        with gzip.GzipFile(fileobj=old_file, mode="wb", mtime=11) as handle:
            handle.write(raw)
        old_blob = old_file.getvalue()
        old_gz = hashlib.sha256(old_blob).hexdigest()
        old_body = json.dumps({
            "source": "msi",
            "jc_day": "2026-11-20",
            "db_sha256": probe["db_sha256"],
            "gz_sha256": old_gz,
        }).encode()
        server = self.tmp / "srv dir"
        server.mkdir()
        db_path = server / "app_latest.db"
        db_path.write_bytes(raw)
        os.chmod(db_path, 0o640)
        env = service_env(self.tmp, server)
        forged = self.tmp / "yuepc.json"
        forged.write_text(json.dumps({
            "source": "yuepc",
            "jc_day": "2026-11-20",
            "db_sha256": probe["db_sha256"],
            "gz_sha256": old_gz,
        }), encoding="utf-8")
        forged_run = subprocess.run(
            ["bash", str(ROOT / "init_server_baseline.sh"), "--receipt", str(forged), "--snapshot", str(db_path)],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(forged_run.returncode, 0, forged_run.stdout + forged_run.stderr)
        self.assertIn("NOT_MSI_RECEIPT", forged_run.stderr)
        self.assertFalse((server / "LAST_COMMIT.json").exists())
        receipt = self.tmp / "msi.json"
        receipt.write_bytes(old_body)
        baseline_run = subprocess.run(
            ["bash", str(ROOT / "init_server_baseline.sh"), "--receipt", str(receipt), "--snapshot", str(db_path)],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(baseline_run.returncode, 0, baseline_run.stdout + baseline_run.stderr)
        baseline = json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["receipt_source"], "msi")
        self.assertEqual(baseline["source"], "msi_receipt")
        self.assertNotIn("yuepc", json.dumps(baseline))
        (self.home / "markers" / "push_base.json").unlink()
        imported = runner.import_push_base(self.home, baseline)
        self.assertTrue(imported["ok"], imported)
        prepared = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        pending = prepared["prepared"][0]
        self.assertEqual(pending["expected_commit_id"], baseline["commit_id"])
        staging = self.home / "staging"
        new_blob = (staging / "app_latest.db.gz").read_bytes()
        self.assertEqual(gzip.decompress(new_blob), raw)
        self.assertEqual(gzip_commit_id(new_blob), baseline["commit_id"])
        self.assertEqual(hashlib.sha256(new_blob).hexdigest(), pending["gz_sha256"])
        self.assertNotEqual(old_gz, pending["gz_sha256"])
        delivered_meta = json.loads((staging / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(delivered_meta["expected_commit_id"], baseline["commit_id"])
        self.assertEqual(delivered_meta["db_sha256"], pending["db_sha256"])
        self.assertEqual(delivered_meta["gz_sha256"], pending["gz_sha256"])
        drop = self.tmp / "drop-new-gz"
        drop.mkdir()
        for name in ("app_latest.db.gz", "app_latest.db.sha256", "app_latest.db.gz.sha256", "meta.json"):
            (drop / name).write_bytes((staging / name).read_bytes())
        (drop / "app_latest.ready").write_text(json.dumps({
            "db_sha256": pending["db_sha256"],
            "gz_sha256": pending["gz_sha256"],
        }), encoding="utf-8")
        (drop / "LAST_PUSHED.json").write_bytes(old_body)
        authorized = subprocess.run(
            ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(authorized.returncode, 0, authorized.stdout + authorized.stderr)
        frozen = json.loads((drop / "push_baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(frozen["expected_commit_id"], baseline["commit_id"])
        drop_a = self.tmp / "drop-a"
        gz_a = stage_drop(drop_a, raw, 11, pending["jc_day"], baseline["commit_id"])
        self.assertEqual(gzip_commit_id((drop_a / "app_latest.db.gz").read_bytes()), baseline["commit_id"])
        authorized_a = subprocess.run(
            ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(drop_a), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(authorized_a.returncode, 0, authorized_a.stdout + authorized_a.stderr)
        done = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("PUSH_COMMITTED", done.stdout)
        self.assertIn("DBTMP=", done.stderr)
        dbtmp = next(line.split("=", 1)[1] for line in done.stderr.splitlines() if line.startswith("DBTMP="))
        self.assertEqual(Path(dbtmp).parent, db_path.parent)
        self.assertNotIn("fat-cn-push.", dbtmp)
        self.assertEqual((drop / "LAST_PUSHED.json.previous").read_bytes(), old_body)
        committed = json.loads((drop / "LAST_PUSHED.json").read_text(encoding="utf-8"))
        self.assertEqual(committed["gz_sha256"], pending["gz_sha256"])
        self.assertEqual(committed["predecessor_commit_id"], baseline["commit_id"])
        self.assertTrue(committed["activated"] is True)
        self.assertEqual((server / "app_latest.db").read_bytes(), raw)
        self.assertEqual((db_path.stat().st_mode & 0o777), 0o640)
        marked = runner.mark_confirmed(
            home=self.home, jc_day=pending["jc_day"], db=self.db, live_root=self.live,
            receipts=[committed],
        )
        self.assertTrue(marked["ok"], marked)
        self.assertFalse((self.home / "markers" / "pending_push.json").exists())
        self.assertTrue((self.home / "markers" / "pushed_2026-11-12.json").is_file())
        newer = b"formal-db-bytes-v2"
        drop_b = self.tmp / "drop-b"
        stage_drop(drop_b, newer, 5, expected_commit_id=committed["commit_id"])
        self.assertEqual(gzip_commit_id((drop_b / "app_latest.db.gz").read_bytes()), committed["commit_id"])
        authorized_b = subprocess.run(
            ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(drop_b), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(authorized_b.returncode, 0, authorized_b.stdout + authorized_b.stderr)
        bound_b = json.loads((drop_b / "push_baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(bound_b["expected_commit_id"], committed["commit_id"])
        pushed_b = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop_b), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(pushed_b.returncode, 0, pushed_b.stdout + pushed_b.stderr)
        self.assertEqual(db_path.read_bytes(), newer)
        self.assertEqual((db_path.stat().st_mode & 0o777), 0o640)
        current_commit = json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))
        self.assertEqual(current_commit["predecessor_commit_id"], committed["commit_id"])
        self.assertEqual(Path(env["FAT_CN_SERVED"]).read_text(encoding="utf-8").strip(), hashlib.sha256(newer).hexdigest())
        (drop_a / "LAST_PUSHED.json").write_bytes((drop_b / "LAST_PUSHED.json").read_bytes())
        late = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop_a), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(late.returncode, 12, late.stdout + late.stderr)
        self.assertIn("STALE_BASELINE", late.stderr)
        self.assertNotIn("COMMIT_JSON", late.stdout)
        self.assertEqual(db_path.read_bytes(), newer)
        self.assertEqual(json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))["commit_id"], current_commit["commit_id"])
        self.assertEqual((drop_a / "LAST_PUSHED.json").read_bytes(), (drop_b / "LAST_PUSHED.json").read_bytes())
        refreshed = json.loads((drop_a / "push_baseline.json").read_text(encoding="utf-8"))
        refreshed["expected_commit_id"] = current_commit["commit_id"]
        (drop_a / "push_baseline.json").write_text(json.dumps(refreshed), encoding="utf-8")
        retried = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop_a), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(retried.returncode, 12, retried.stdout + retried.stderr)
        self.assertEqual(db_path.read_bytes(), newer)
        rebound = subprocess.run(
            ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(drop_a), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(rebound.returncode, 12, rebound.stdout + rebound.stderr)
        self.assertIn("STALE_BASELINE", rebound.stderr)
        stored = json.loads((server / "authorized" / f"{gz_a}.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["expected_commit_id"], baseline["commit_id"])
        still = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop_a), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(still.returncode, 12, still.stdout + still.stderr)
        self.assertEqual(db_path.read_bytes(), newer)
        waiter = self.tmp / "drop-wait"
        gz_wait = stage_drop(waiter, newer, 3)
        (waiter / "push_baseline.json").write_text(json.dumps({
            "db_sha256": hashlib.sha256(newer).hexdigest(),
            "gz_sha256": gz_wait,
            "expected_commit_id": current_commit["commit_id"],
            "expected_db_sha256": hashlib.sha256(newer).hexdigest(),
        }), encoding="utf-8")
        env_wait = dict(env)
        env_wait["FAT_CN_PAUSE_SEC"] = "2"
        first = subprocess.Popen(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(waiter), "--ssh-target", "local"],
            env=env_wait, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        try:
            import select
            fd = first.stderr.fileno()
            os.set_blocking(fd, False)
            seen = b""
            deadline = time.time() + 5
            while b"WORKDIR=" not in seen and time.time() < deadline:
                ready_fds, _, _ = select.select([fd], [], [], 0.2)
                if ready_fds:
                    seen += os.read(fd, 4096)
            self.assertIn(b"WORKDIR=", seen)
            rival = self.tmp / "drop-rival"
            gz_rival = stage_drop(rival, newer, 4)
            (rival / "push_baseline.json").write_text((waiter / "push_baseline.json").read_text(encoding="utf-8").replace(gz_wait, gz_rival), encoding="utf-8")
            blocked = subprocess.run(
                ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(rival), "--ssh-target", "local"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(blocked.returncode, 9, blocked.stdout + blocked.stderr)
            self.assertIn("REMOTE_PUSH_LOCK_HELD", blocked.stderr)
            self.assertTrue((rival / "app_latest.ready").is_file())
            same = subprocess.run(
                ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(waiter), "--ssh-target", "local"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(same.returncode, 11, same.stdout + same.stderr)
            self.assertIn("DROP_LOCK_HELD", same.stderr)
            self.assertTrue((waiter / "app_latest.ready").is_file())
        finally:
            os.killpg(first.pid, signal.SIGKILL)
            first.wait(timeout=5)
            first.stdout.close()
            first.stderr.close()

    def _initialized_package(self, name: str, current: bytes, replacement: bytes, mode: str):
        server = self.tmp / name / "server"
        server.mkdir(parents=True)
        db_path = server / "app_latest.db"
        db_path.write_bytes(current)
        env = service_env(self.tmp / name, server, mode)
        current_sha = hashlib.sha256(current).hexdigest()
        receipt = self.tmp / name / "msi.json"
        receipt.parent.mkdir(exist_ok=True)
        receipt.write_text(json.dumps({
            "source": "msi",
            "jc_day": "2026-11-20",
            "db_sha256": current_sha,
            "gz_sha256": "ab" * 32,
        }), encoding="utf-8")
        baseline = subprocess.run(
            ["bash", str(ROOT / "init_server_baseline.sh"), "--receipt", str(receipt), "--snapshot", str(db_path)],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)
        commit_id = json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))["commit_id"]
        drop = self.tmp / name / "drop"
        stage_drop(drop, replacement, 0, expected_commit_id=commit_id)
        (Path(env["FAT_CN_SERVED"])).write_text(current_sha + "\n", encoding="utf-8")
        return server, db_path, env, drop

    def test_old_connection_restores_service(self):
        server, db_path, env, drop = self._initialized_package("old-conn", b"current-db", b"replacement-db", "old_connection")
        authorized = subprocess.run(
            ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(authorized.returncode, 0, authorized.stdout + authorized.stderr)
        before = json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))["commit_id"]
        failed = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(failed.returncode, 13, failed.stdout + failed.stderr)
        self.assertIn("activate proof failed", failed.stderr)
        self.assertNotIn("COMMIT_JSON", failed.stdout)
        self.assertEqual(db_path.read_bytes(), b"current-db")
        self.assertEqual(Path(env["FAT_CN_SERVED"]).read_text(encoding="utf-8").strip(), hashlib.sha256(b"current-db").hexdigest())
        self.assertEqual(json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))["commit_id"], before)
        log = Path(env["FAT_CN_HOOK_LOG"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(log, ["activate", "rollback"])
        self.assertFalse((server / "CN_PUSH_ALERT.json").exists())

    def test_rollback_failure_leaves_alert_without_receipt(self):
        server, db_path, env, drop = self._initialized_package("bad-rollback", b"current-db", b"replacement-db", "rollback_lies")
        authorized = subprocess.run(
            ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(authorized.returncode, 0, authorized.stdout + authorized.stderr)
        failed = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(failed.returncode, 16, failed.stdout + failed.stderr)
        self.assertIn("CN_PUSH_ALERT", failed.stderr)
        self.assertNotIn("COMMIT_JSON", failed.stdout)
        self.assertEqual(db_path.read_bytes(), b"current-db")
        alert = json.loads((server / "CN_PUSH_ALERT.json").read_text(encoding="utf-8"))
        self.assertEqual(alert["severity"], "critical")
        self.assertFalse(alert["receipt_issued"])
        self.assertEqual(alert["reason"], "service_rollback_unverified")

    def test_missing_activate_hook_does_not_replace(self):
        server, db_path, env, drop = self._initialized_package("no-hook", b"current-db", b"replacement-db", "match_file")
        env.pop("FAT_CN_ACTIVATE_CMD")
        env.pop("FAT_CN_ROLLBACK_CMD")
        authorized = subprocess.run(
            ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(authorized.returncode, 0, authorized.stdout + authorized.stderr)
        failed = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(failed.returncode, 14, failed.stdout + failed.stderr)
        self.assertIn("ACTIVATE_HOOK_MISSING", failed.stderr)
        self.assertEqual(db_path.read_bytes(), b"current-db")
        self.assertFalse((drop / "LAST_PUSHED.json").exists())

    def test_missing_ssh_target_does_not_run_locally(self):
        drop = self.tmp / "no-ssh"
        failed = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop)],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
        self.assertIn("SSH_TARGET_REQUIRED", failed.stderr)
        self.assertFalse(drop.exists())


    def test_unbound_old_package_is_not_authorized_after_successor(self):
        server, db_path, env, _bound = self._initialized_package(
            "late-auth", b"db-b-live", b"db-a-old", "match_file",
        )
        live = json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))
        old = self.tmp / "late-auth" / "old-a"
        stage_drop(old, b"db-a-old", 11)
        (old / "push_baseline.json").write_text(json.dumps({
            "db_sha256": hashlib.sha256(b"db-a-old").hexdigest(),
            "gz_sha256": hashlib.sha256((old / "app_latest.db.gz").read_bytes()).hexdigest(),
            "expected_commit_id": live["commit_id"],
            "expected_db_sha256": live["db_sha256"],
        }), encoding="utf-8")
        failed = subprocess.run(
            ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(old), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(failed.returncode, 18, failed.stdout + failed.stderr)
        self.assertIn("PACKAGE_UNBOUND", failed.stderr)
        self.assertNotIn("COMMIT_JSON", failed.stdout)
        self.assertNotIn("BASELINE_JSON", failed.stdout)
        self.assertEqual(db_path.read_bytes(), b"db-b-live")
        self.assertFalse((old / "LAST_PUSHED.json").exists())
        self.assertFalse((server / "authorized").exists())
        self.assertEqual(
            json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))["commit_id"],
            live["commit_id"],
        )

    def test_activate_hook_must_read_the_running_db(self):
        _server, db_path, env, drop = self._initialized_package(
            "echo-hook", b"current-db", b"replacement-db", "match_file",
        )
        echo = Path(env["FAT_CN_ACTIVATE_CMD"]).with_name("echo.sh")
        echo.write_text(
            "#!/bin/sh\n"
            "python3 - <<'PY'\n"
            "import json, os\n"
            "print(json.dumps({'ok': True, 'db_sha256': os.environ.get('FAT_CN_EXPECT_DB_SHA', ''), 'read_only': True}))\n"
            "PY\n",
            encoding="utf-8",
        )
        echo.chmod(0o755)
        env["FAT_CN_ACTIVATE_CMD"] = str(echo)
        authorized = subprocess.run(
            ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(authorized.returncode, 0, authorized.stdout + authorized.stderr)
        failed = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        self.assertNotIn("COMMIT_JSON", failed.stdout)
        self.assertEqual(db_path.read_bytes(), b"current-db")
        self.assertFalse((drop / "LAST_PUSHED.json").exists())

    def test_missing_or_bad_push_base_does_not_publish(self):
        make_db(self.db, historical_days())
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        base = self.home / "markers" / "push_base.json"
        base.unlink()
        staging = self.home / "staging"
        staging.mkdir()
        (staging / "app_latest.db.gz").write_bytes(b"KEEP")
        missing = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"], "push_base_missing")
        self.assertEqual((staging / "app_latest.db.gz").read_bytes(), b"KEEP")
        self.assertFalse((self.home / "markers" / "pending_push.json").exists())
        base.write_text("{", encoding="utf-8")
        corrupt = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertEqual(corrupt["error"], "push_base_invalid")
        self.assertEqual(base.read_text(encoding="utf-8"), "{")
        self.assertEqual((staging / "app_latest.db.gz").read_bytes(), b"KEEP")
        write_json(base, {"commit_id": ""})
        empty = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertEqual(empty["error"], "push_base_invalid")
        self.assertFalse((self.home / "markers" / "pending_push.json").exists())
        base.unlink()
        forged = runner.import_push_base(self.home, {"commit_id": "not-a-server-commit", "kind": "baseline"})
        self.assertFalse(forged["ok"])
        self.assertEqual(forged["reason"], "baseline_untrusted")
        self.assertFalse(base.exists())

    def test_two_prepare_rounds_use_confirmed_commit(self):
        base = self.home / "markers" / "push_base.json"
        base.unlink()
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        make_db(self.db, {
            "2026-11-11": "complete",
            "2026-11-12": "complete",
            "2026-11-13": "none",
            "2026-11-14": "incomplete",
            "2026-11-21": "incomplete",
        })
        server = self.tmp / "round-server"
        server.mkdir()
        db_path = server / "app_latest.db"
        db_path.write_bytes(b"live-base")
        db_sha = hashlib.sha256(b"live-base").hexdigest()
        receipt = self.tmp / "round-msi.json"
        receipt.write_text(json.dumps({
            "source": "msi",
            "jc_day": "2026-11-11",
            "db_sha256": db_sha,
            "gz_sha256": "cd" * 32,
        }), encoding="utf-8")
        env = service_env(self.tmp / "round-env", server)
        init = subprocess.run(
            ["bash", str(ROOT / "init_server_baseline.sh"), "--receipt", str(receipt), "--snapshot", str(db_path)],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        imported = runner.import_push_base(
            self.home, json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8")),
        )
        self.assertTrue(imported["ok"], imported)

        def deliver_and_confirm(drop: Path):
            drop.mkdir()
            staging = self.home / "staging"
            for name in ("app_latest.db.gz", "app_latest.db.sha256", "app_latest.db.gz.sha256", "meta.json"):
                shutil.copy(staging / name, drop / name)
            meta = json.loads((drop / "meta.json").read_text(encoding="utf-8"))
            (drop / "app_latest.ready").write_text(json.dumps({
                "db_sha256": meta["db_sha256"],
                "gz_sha256": meta["gz_sha256"],
            }), encoding="utf-8")
            pushed = subprocess.run(
                ["bash", str(ROOT / "push_cn_package.sh"), "--dir", str(drop), "--ssh-target", "local"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(pushed.returncode, 0, pushed.stdout + pushed.stderr)
            self.assertIn("BASELINE_AUTHORIZED", pushed.stdout)
            self.assertIn("PUSH_COMMITTED", pushed.stdout)
            commit = json.loads((drop / "LAST_PUSHED.json").read_text(encoding="utf-8"))
            marked = runner.mark_confirmed(
                home=self.home, jc_day=meta["jc_day"], db=self.db, live_root=self.live, receipts=[commit],
            )
            self.assertTrue(marked["ok"], marked)
            return meta, commit

        first = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["prepared"][0]["expected_commit_id"], imported["commit_id"])
        meta1, commit1 = deliver_and_confirm(self.tmp / "round-drop-1")
        self.assertEqual(commit1["predecessor_commit_id"], imported["commit_id"])
        self.assertEqual(gzip_commit_id((self.tmp / "round-drop-1" / "app_latest.db.gz").read_bytes()), imported["commit_id"])
        advanced = json.loads(base.read_text(encoding="utf-8"))
        self.assertEqual(advanced["commit_id"], commit1["commit_id"])
        self.assertEqual(advanced["source"], "server_commit")
        self.db.unlink()
        make_db(self.db, {
            "2026-11-11": "complete",
            "2026-11-12": "complete",
            "2026-11-13": "none",
            "2026-11-14": "complete",
            "2026-11-21": "incomplete",
        })
        second = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["prepared"][0]["expected_commit_id"], commit1["commit_id"])
        self.assertIn("2026-11-14", second["prepared"][0]["coverage"]["claimed_jc_days"])
        meta2, commit2 = deliver_and_confirm(self.tmp / "round-drop-2")
        self.assertEqual(meta2["expected_commit_id"], commit1["commit_id"])
        self.assertEqual(commit2["predecessor_commit_id"], commit1["commit_id"])
        self.assertEqual(gzip.decompress((self.tmp / "round-drop-2" / "app_latest.db.gz").read_bytes()), db_path.read_bytes())
        self.assertEqual(json.loads(base.read_text(encoding="utf-8"))["commit_id"], commit2["commit_id"])

    def test_bound_package_never_authorized_is_rejected_after_successor(self):
        base = self.home / "markers" / "push_base.json"
        base.unlink()
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        make_db(self.db, {
            "2026-11-11": "complete",
            "2026-11-12": "complete",
            "2026-11-21": "incomplete",
        })
        server = self.tmp / "held-server"
        server.mkdir()
        db_path = server / "app_latest.db"
        db_path.write_bytes(b"held-base")
        db_sha = hashlib.sha256(b"held-base").hexdigest()
        receipt = self.tmp / "held-msi.json"
        receipt.write_text(json.dumps({
            "source": "msi",
            "jc_day": "2026-11-11",
            "db_sha256": db_sha,
            "gz_sha256": "ef" * 32,
        }), encoding="utf-8")
        env = service_env(self.tmp / "held-env", server)
        init = subprocess.run(
            ["bash", str(ROOT / "init_server_baseline.sh"), "--receipt", str(receipt), "--snapshot", str(db_path)],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        imported = runner.import_push_base(self.home, json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8")))
        self.assertTrue(imported["ok"], imported)
        held = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertTrue(held["ok"], held)
        saved = self.tmp / "held-a"
        saved.mkdir()
        for name in ("app_latest.db.gz", "app_latest.db.sha256", "app_latest.db.gz.sha256", "meta.json"):
            shutil.copy(self.home / "staging" / name, saved / name)
        meta = json.loads((saved / "meta.json").read_text(encoding="utf-8"))
        (saved / "app_latest.ready").write_text(json.dumps({
            "db_sha256": meta["db_sha256"], "gz_sha256": meta["gz_sha256"],
        }), encoding="utf-8")
        self.assertEqual(gzip_commit_id((saved / "app_latest.db.gz").read_bytes()), imported["commit_id"])
        (self.home / "markers" / "pending_push.json").unlink()
        for extra in (self.home / "markers").glob("pending_2026-*.json"):
            extra.unlink()
        self.db.unlink()
        make_db(self.db, {
            "2026-11-11": "complete",
            "2026-11-12": "complete",
            "2026-11-13": "complete",
            "2026-11-21": "incomplete",
        })
        successor = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertTrue(successor["ok"], successor)
        drop_b = self.tmp / "held-b"
        drop_b.mkdir()
        for name in ("app_latest.db.gz", "app_latest.db.sha256", "app_latest.db.gz.sha256", "meta.json"):
            shutil.copy(self.home / "staging" / name, drop_b / name)
        meta_b = json.loads((drop_b / "meta.json").read_text(encoding="utf-8"))
        (drop_b / "app_latest.ready").write_text(json.dumps({
            "db_sha256": meta_b["db_sha256"], "gz_sha256": meta_b["gz_sha256"],
        }), encoding="utf-8")
        pushed = subprocess.run(
            ["bash", str(ROOT / "push_cn_package.sh"), "--dir", str(drop_b), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(pushed.returncode, 0, pushed.stdout + pushed.stderr)
        live_bytes = db_path.read_bytes()
        live_commit = json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))["commit_id"]
        refused = subprocess.run(
            ["bash", str(ROOT / "push_cn_package.sh"), "--dir", str(saved), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(refused.returncode, 12, refused.stdout + refused.stderr)
        self.assertIn("STALE_BASELINE", refused.stderr)
        self.assertNotIn("PUSH_COMMITTED", refused.stdout)
        self.assertNotIn("COMMIT_JSON", refused.stdout)
        self.assertFalse((saved / "LAST_PUSHED.json").exists())
        self.assertEqual(db_path.read_bytes(), live_bytes)
        self.assertEqual(json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))["commit_id"], live_commit)
        self.assertFalse((server / "authorized" / f"{meta['gz_sha256']}.json").exists())

    def test_install_lock_is_held_before_push_reads(self):
        drop = self.tmp / "drop-install"
        drop.mkdir()
        ready = drop / "app_latest.ready"
        ready.write_text("OLD", encoding="utf-8")
        holder = subprocess.Popen(
            ["bash", str(ROOT / "hold_drop_lock.sh"), str(drop)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "DROP_LOCK_HELD_OK")
            ready.write_text("B-READY", encoding="utf-8")
            blocked = subprocess.run(
                ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop)],
                env={**os.environ, "FAT_CN_ALLOW_TEST_LOCAL": "1"},
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(blocked.returncode, 11, blocked.stdout + blocked.stderr)
            self.assertIn("DROP_LOCK_HELD", blocked.stderr)
            self.assertFalse((drop / "commit.stdout").exists())
            self.assertEqual(ready.read_text(encoding="utf-8"), "B-READY")
        finally:
            holder.stdin.close()
            holder.wait(timeout=5)
            holder.stdout.close()
            holder.stderr.close()

    def test_missing_flock_is_dependency_not_lock(self):
        partial = self.tmp / "partial-bin"
        partial.mkdir()
        os.symlink("/bin/mkdir", partial / "mkdir")
        drop = self.tmp / "drop-no-flock"
        env = os.environ.copy()
        env["PATH"] = str(partial)
        failed = subprocess.run(
            ["/bin/bash", str(ROOT / "hold_drop_lock.sh"), str(drop)],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(failed.returncode, 20, failed.stdout + failed.stderr)
        self.assertIn("DEPENDENCY_MISSING", failed.stderr)
        self.assertNotIn("DROP_LOCK_HELD", failed.stderr)

    def test_non_ascii_powershell_files_keep_utf8_bom(self):
        bom = b"\xef\xbb\xbf"
        required = {
            "windows_drop_lock.ps1",
            "windows_isolation_check.ps1",
            "windows_argv.ps1",
            "windows_stdin_frame_check.ps1",
            "push-formal-db.ps1",
            "ypc_watch_and_push.ps1",
        }
        seen = set()
        for path in ROOT.glob("*.ps1"):
            raw = path.read_bytes()
            if raw.startswith(bom):
                raw[3:].decode("utf-8")
                seen.add(path.name)
            else:
                raw.decode("ascii")
        missing = required - seen
        self.assertFalse(missing, sorted(missing))
        isolation = (ROOT / "windows_isolation_check.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Format-FatWinCommandLine", isolation)
        self.assertNotIn("-ArgumentList", isolation)
        self.assertIn("PS51_PARSE_OK", isolation)
        self.assertIn("ECHO_DROP=", isolation)
        self.assertNotIn("ISOLATION_OK", isolation)
        argv = (ROOT / "windows_argv.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Invoke-FatWithNoBomInputEncoding", argv)
        self.assertIn("UTF8Encoding $false", argv)
        self.assertIn("GetPreamble()", argv)
        self.assertNotIn("haveWrittenPreamble", argv)
        push = (ROOT / "push-formal-db.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Invoke-FatWithNoBomInputEncoding", push)
        self.assertIn("Copy-FatRawStdin", push)
        frame = (ROOT / "windows_stdin_frame_check.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("UTF8Encoding $true", frame)
        self.assertIn("InputEncoding = $oldIn", frame)
        self.assertIn("SEPARATE_PROCESS_FRAME_OK", frame)

    def test_powershell_variables_do_not_differ_only_by_case(self):
        import re
        for path in ROOT.glob("*.ps1"):
            text = path.read_text(encoding="utf-8-sig")
            text = re.sub(r"(?s)@'.*?'@", " ", text)
            text = text.replace("`$", "")
            scopes = re.split(r"(?m)^function\s+\w+[^\n]*\{", text)
            for scope in scopes:
                groups = {}
                for name in re.findall(r"\$(\w+)", scope):
                    groups.setdefault(name.casefold(), set()).add(name)
                clashes = {key: sorted(value) for key, value in groups.items() if len(value) > 1}
                self.assertFalse(clashes, (path.name, clashes))

    def test_check_delivery_cli_returns_decision(self):
        db_sha = "ab" * 32
        gz_sha = "cd" * 32
        (self.home / "staging").mkdir()
        write_json(self.home / "markers" / "pending_push.json", {
            "jc_day": "2026-11-12", "db_sha256": db_sha, "gz_sha256": gz_sha,
        })
        write_json(self.home / "staging" / "meta.json", {
            "jc_day": "2026-11-12", "db_sha256": db_sha, "gz_sha256": gz_sha,
        })
        receipt = self.tmp / "receipt.json"
        write_json(receipt, {"source": "msi", "db_sha256": db_sha, "gz_sha256": gz_sha})
        completed = subprocess.run(
            [
                "python3", str(ROOT / "run_auto_cn_push.py"),
                "--check-delivery",
                "--home", str(self.home),
                "--db", str(self.db),
                "--live-root", str(self.live),
                "--receipt", str(receipt),
            ],
            text=True, capture_output=True, check=False,
        )
        self.assertNotIn("NameError", completed.stderr + completed.stdout)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        decision = json.loads(completed.stdout)
        self.assertTrue(decision["allow_mark"])
        self.assertEqual(decision["reason"], "receipt_match")

    def test_server_header_accepts_utf8_bom_without_touching_gz(self):
        import io
        raw = b"db-bytes-for-bom-header"
        blob_path = self.tmp / "bom.db.gz"
        write_db_gzip(raw, blob_path, "baseline-bom", mtime=0)
        blob = blob_path.read_bytes()
        db_sha = hashlib.sha256(raw).hexdigest()
        gz_sha = hashlib.sha256(blob).hexdigest()
        header = (
            '{"op":"authorize","jc_day":"2026-11-20","db_sha256":"%s","gz_sha256":"%s","expected_commit_id":"baseline-bom"}\n'
            % (db_sha, gz_sha)
        ).encode("utf-8")
        payload = b"\xef\xbb\xbf" + header + blob
        self.assertEqual(payload[:3], b"\xef\xbb\xbf")
        self.assertEqual(payload[3:5], b"{\"")
        completed = subprocess.run(
            ["bash", str(ROOT / "cn_push_transaction.sh")],
            input=payload,
            capture_output=True,
            check=False,
        )
        err = completed.stderr.decode("utf-8", "replace")
        self.assertNotIn("Unexpected UTF-8 BOM", err)
        self.assertNotIn("json.decoder", err)
        self.assertIn("BASELINE_NOT_FROZEN", err)

    def test_disk_hash_is_not_a_running_api_fingerprint(self):
        _server, db_path, env, drop = self._initialized_package(
            "disk-fingerprint", b"current-db", b"replacement-db", "disk_only",
        )
        authorized = subprocess.run(
            ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(authorized.returncode, 0, authorized.stdout + authorized.stderr)
        failed = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        self.assertIn("running api", failed.stderr)
        self.assertNotIn("COMMIT_JSON", failed.stdout)
        self.assertEqual(db_path.read_bytes(), b"current-db")
        self.assertFalse((drop / "LAST_PUSHED.json").exists())

    def test_receipt_migration_does_not_fabricate_middle_days(self):
        make_db(self.db, {
            "2026-11-12": "complete",
            "2026-11-13": "none",
            "2026-11-14": "complete",
            "2026-11-15": "incomplete",
            "2026-11-16": "complete",
            "2026-11-20": "complete",
        })
        import hashlib
        digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        receipt = {"source": "msi", "jc_day": "2026-11-20", "db_sha256": digest, "gz_sha256": "b" * 64}
        from migrate_receipt import migrate_receipt
        missing = migrate_receipt(
            home=self.home, receipt=receipt, snapshot=None, live_root=self.live,
            today=TODAY, start=date(2026, 11, 12),
        )
        self.assertEqual(missing["status"], "needs_evidence")
        self.assertEqual(list((self.home / "markers").glob("pushed_2026-11-1*.json")), [])
        other = self.tmp / "other.db"
        other.write_bytes(b"different")
        mismatched = migrate_receipt(
            home=self.home, receipt=receipt, snapshot=other, live_root=self.live,
            today=TODAY, start=date(2026, 11, 12),
        )
        self.assertEqual(mismatched["status"], "snapshot_hash_mismatch")
        self.assertEqual(list((self.home / "markers").glob("pushed_*.json")), [])
        plan = self.live / "plan"
        plan.mkdir(parents=True)
        write_json(plan / "2026-11-12.json", {"kickoff_log": [
            {"event": "dropped_from_csl", "match_uid": "2026-11-12|m0"},
            {"event": "dropped_from_csl", "match_uid": "2026-11-12|m1"},
        ]})
        write_json(self.home / "markers" / "gap_ledger.json", {
            "kind": "gap_ledger",
            "days": [{"jc_day": "2026-10-01", "reason": "db_incomplete"}],
        })
        migrated = migrate_receipt(
            home=self.home, receipt=receipt, snapshot=self.db, live_root=self.live,
            today=TODAY, start=date(2026, 11, 12),
        )
        self.assertIn("2026-11-12", migrated["marked"])
        self.assertIn("2026-11-16", migrated["marked"])
        self.assertNotIn("2026-11-15", migrated["marked"])
        self.assertIn("2026-11-13", migrated["no_matches"])
        saved = json.loads((self.home / "markers" / "pushed_2026-11-12.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["evidence_source"], "historical_snapshot")
        self.assertEqual(saved["evidence_db_sha256"], digest)
        self.assertTrue(saved["not_a_new_push"])

        self.assertEqual(saved["evidence_source"], "historical_snapshot")
        self.assertEqual(migrated.get("dropped_from_csl_snapshot"), "absent")
        ledger = json.loads((self.home / "markers" / "gap_ledger.json").read_text(encoding="utf-8"))
        self.assertIn("2026-10-01", [item["jc_day"] for item in ledger["days"]])
        current = migrate_receipt(
            home=self.home, receipt=receipt, snapshot=self.db, live_root=self.live,
            today=TODAY, start=date(2026, 11, 12), as_current=True,
        )
        self.assertEqual(current["status"], "current_evidence_not_pushed")
        self.assertEqual(current["marked"], [])
        self.assertTrue((self.home / "markers" / "evidence_current_2026-11-15.json").is_file())
        evidence = json.loads((self.home / "markers" / "evidence_current_2026-11-15.json").read_text(encoding="utf-8"))
        self.assertTrue(evidence["not_pushed"])
        self.assertFalse((self.home / "markers" / "pushed_2026-11-15.json").exists())

    def test_quarantine_hold_blocks_later_prepare(self):
        staging = self.home / "staging"
        staging.mkdir()
        (staging / "app_latest.db.gz").write_bytes(b"KEEP")
        (self.home / "markers" / "pending_push.json").write_text('{"jc_day":', encoding="utf-8")
        make_db(self.db, historical_days())
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        first = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertEqual(first["error"], "pending_corrupt")
        second = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertEqual(second["error"], "quarantine_hold")
        self.assertEqual((staging / "app_latest.db.gz").read_bytes(), b"KEEP")
        released = runner.release_quarantine(self.home, "审计后确认可以重新打包")
        self.assertTrue(released["ok"])
        self.assertTrue((self.home / "markers" / "quarantine_audit.jsonl").is_file())
        third = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        self.assertTrue(third["ok"], third)
        self.assertNotEqual((staging / "app_latest.db.gz").read_bytes(), b"KEEP")

    def test_wal_insert_is_not_hidden_by_empty_cache(self):
        make_db(self.db, {})
        con = sqlite3.connect(self.db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.commit()
        con.close()
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        runner.plan_coverage(
            home=self.home, markers=self.home / "markers", db=self.db, live_root=self.live,
            today=TODAY, lookback=14,
        )
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO matches (id, match_uid) VALUES (1, '2026-11-13|m0')")
        con.commit()
        con.close()
        stat = self.db.stat()
        write_json(self.home / "markers" / "checked_no_matches_2026-11-13.json", {
            "jc_day": "2026-11-13",
            "reason": "no_matches",
            "n_matches": 0,
            "db_mtime_ns": stat.st_mtime_ns,
            "db_size": stat.st_size,
        })
        planned = runner.plan_coverage(
            home=self.home, markers=self.home / "markers", db=self.db, live_root=self.live,
            today=TODAY, lookback=14,
        )
        row = next(item for item in planned["reports"] if item["jc_day"] == "2026-11-13")
        self.assertEqual(row["n_matches"], 1)
        self.assertNotEqual(row.get("reason"), "no_matches")

    def test_crontab_conflict_and_verify_do_not_restore_table(self):
        binary, state = self._fake_crontab("jobs")
        # 第二次读取与第一次不同，必须停止且不写。
        path = binary
        path.write_text(
            "#!/bin/bash\n"
            f"STATE='{state}'\n"
            'COUNT_FILE="$STATE.count"\n'
            'N=0; if [[ -f "$COUNT_FILE" ]]; then N=$(cat "$COUNT_FILE"); fi\n'
            'N=$((N+1)); echo "$N" > "$COUNT_FILE"\n'
            'if [[ "$1" == "-l" ]]; then\n'
            '  if [[ "$N" -eq 1 ]]; then printf "%s\\n" "KEEP" "GONE"; exit 0; fi\n'
            '  printf "%s\\n" "KEEP"; exit 0\n'
            "fi\n"
            'cat > "$STATE"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        state.write_text("KEEP\nGONE\n", encoding="utf-8")
        before = state.read_text(encoding="utf-8")
        failed = subprocess.run(
            ["bash", str(ROOT / "install_watchdog.sh")],
            env=self._install_env(path), text=True, capture_output=True, check=False,
        )
        self.assertEqual(failed.returncode, 8, failed.stdout + failed.stderr)
        self.assertEqual(state.read_text(encoding="utf-8"), before)
        # 写后表被并发改成 OTHER，修复只能基于新表加本行，不能把 KEEP/GONE 整表写回。
        path.write_text(
            "#!/bin/bash\n"
            f"STATE='{state}'\n"
            'COUNT_FILE="$STATE.writes"\n'
            'if [[ "$1" == "-l" ]]; then\n'
            '  if [[ -f "$STATE" ]]; then cat "$STATE"; else printf "%s\\n" "KEEP"; fi\n'
            "  exit 0\n"
            "fi\n"
            'echo x >> "$COUNT_FILE"\n'
            'N=$(wc -l < "$COUNT_FILE")\n'
            'if [[ "$N" -eq 1 ]]; then printf "%s\\n" "OTHER" > "$STATE"; else cat > "$STATE"; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        state.write_text("KEEP\n", encoding="utf-8")
        retried = subprocess.run(
            ["bash", str(ROOT / "install_watchdog.sh")],
            env=self._install_env(path), text=True, capture_output=True, check=False,
        )
        self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
        final = state.read_text(encoding="utf-8")
        self.assertIn("OTHER", final)
        self.assertIn("# fat-auto-cn-push-watchdog", final)
        self.assertNotIn("GONE", final)

    def test_legacy_msi_receipt_migrates_without_rewriting_source(self):
        raw = b"legacy-msi-snapshot"
        db_sha = hashlib.sha256(raw).hexdigest()
        gz_sha = "cd" * 32
        receipt = {
            "jc_day": "2026-11-20",
            "pushed_at": "2026-11-21T03:40:00+08:00",
            "db_sha256": db_sha,
            "gz_sha256": gz_sha,
            "backup": "kept-backup-name",
        }
        path = self.tmp / "LAST_PUSHED.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False) + "\n", encoding="utf-8")
        original = path.read_bytes()
        file_sha = hashlib.sha256(original).hexdigest()
        server = self.tmp / "legacy-server"
        server.mkdir()
        db_path = server / "app_latest.db"
        db_path.write_bytes(raw)
        env = service_env(self.tmp / "legacy-env", server)
        script = ["bash", str(ROOT / "migrate_legacy_msi_baseline.sh")]

        def migrate(receipt_path=path, sha=file_sha, evidence="msi", snapshot=db_path):
            return subprocess.run(
                script + [
                    "--receipt", str(receipt_path),
                    "--snapshot", str(snapshot),
                    "--evidence-source", evidence,
                    "--receipt-sha256", sha,
                ],
                env=env, text=True, capture_output=True, check=False,
            )

        yue = self.tmp / "yuepc-old.json"
        yue_body = dict(receipt)
        yue_body["source"] = "yuepc"
        yue.write_text(json.dumps(yue_body, ensure_ascii=False) + "\n", encoding="utf-8")
        yue_sha = hashlib.sha256(yue.read_bytes()).hexdigest()
        refused = migrate(yue, yue_sha)
        self.assertEqual(refused.returncode, 19, refused.stdout + refused.stderr)
        self.assertIn("NOT_MSI_RECEIPT", refused.stderr)
        self.assertFalse((server / "LAST_COMMIT.json").exists())
        bad_evidence = migrate(evidence="yuepc")
        self.assertEqual(bad_evidence.returncode, 19, bad_evidence.stderr)
        self.assertIn("EVIDENCE_SOURCE_REFUSED", bad_evidence.stderr)
        bad_file = migrate(sha="ab" * 32)
        self.assertEqual(bad_file.returncode, 19, bad_file.stderr)
        self.assertIn("RECEIPT_BYTES_MISMATCH", bad_file.stderr)
        other = self.tmp / "other-snap.db"
        other.write_bytes(b"different-db")
        bad_snap = migrate(snapshot=other)
        self.assertEqual(bad_snap.returncode, 19, bad_snap.stderr)
        self.assertIn("BASELINE_HASH_MISMATCH", bad_snap.stderr)
        self.assertFalse((server / "LAST_COMMIT.json").exists())
        self.assertEqual(path.read_bytes(), original)
        init = subprocess.run(
            ["bash", str(ROOT / "init_server_baseline.sh"), "--receipt", str(path), "--snapshot", str(db_path)],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertIn("NOT_MSI_RECEIPT", init.stderr)
        self.assertEqual(path.read_bytes(), original)
        migrated = migrate()
        self.assertEqual(migrated.returncode, 0, migrated.stdout + migrated.stderr)
        self.assertEqual(path.read_bytes(), original)
        self.assertNotIn("source", json.loads(path.read_text(encoding="utf-8")))
        record = json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))
        audit = json.loads((server / "legacy_msi_baseline_audit.json").read_text(encoding="utf-8"))
        commit_id = runner._baseline_commit_id("2026-11-20", db_sha, gz_sha)
        self.assertEqual(record["commit_id"], commit_id)
        self.assertTrue(record["legacy_migration"])
        self.assertFalse(record["receipt_had_source"])
        self.assertEqual(record["evidence_source"], "msi")
        self.assertEqual(record["receipt_file_sha256"], file_sha)
        self.assertEqual(record["original_fields"], ["backup", "db_sha256", "gz_sha256", "jc_day", "pushed_at"])
        self.assertEqual(audit["kind"], "legacy_msi_baseline_audit")
        self.assertEqual(audit["receipt_file_sha256"], file_sha)
        self.assertNotIn("source", audit["original_fields"])
        home = self.tmp / "legacy-home"
        (home / "markers").mkdir(parents=True)
        imported = runner.import_push_base(home, record)
        self.assertTrue(imported["ok"], imported)
        base = json.loads((home / "markers" / "push_base.json").read_text(encoding="utf-8"))
        self.assertEqual(base["commit_id"], commit_id)
        self.assertFalse(base["receipt_had_source"])
        self.assertEqual(base["receipt_file_sha256"], file_sha)
        self.assertFalse(list((home / "markers").glob("pushed_*.json")))
        again = migrate()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(path.read_bytes(), original)
        stripped = dict(record)
        stripped["receipt_file_sha256"] = ""
        rejected = runner.import_push_base(self.tmp / "stripped-home", stripped)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["reason"], "baseline_untrusted")
        self.assertFalse((self.tmp / "stripped-home" / "markers" / "push_base.json").exists())

    def test_content_identity_preserves_types_and_middle_rows(self):
        same_edge = ("HEAD", "TAIL")
        old = self._middle_db("OLD!")
        new = self._middle_db("NEW!")
        self.assertEqual(self._periphery(old), self._periphery(new))
        self.assertNotEqual(self._file_fingerprint(old), self._file_fingerprint(new))
        before = old.read_bytes()
        self._file_fingerprint(old)
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(same_edge[0], self._periphery(old)[2])
        pairs = (
            ([("ab", "c")], [("a", "bc")]),
            ([(None, "")], [("", None)]),
            ([(None, None)], [("", "")]),
            ([("", b"")], [(b"", "")]),
            ([(1, None)], [(1.0, None)]),
            ([(1, None)], [("1", None)]),
        )
        for left_rows, right_rows in pairs:
            with self.subTest(left=left_rows, right=right_rows):
                self.assertNotEqual(
                    self._file_fingerprint(self._value_db(left_rows)),
                    self._file_fingerprint(self._value_db(right_rows)),
                )

    def test_health_db_asgi_rejects_before_connect(self):
        token = secrets.token_urlsafe(16)
        cases = (
            ("asgi", "0", "", "", False),
            ("fastapi", "0", "", "", False),
            ("asgi", "0", token, "wrong-token", False),
            ("fastapi", "0", token, token, False),
            ("asgi", "1", token, "", True),
            ("fastapi", "1", token, token, True),
        )
        for kind, readonly, file_token, header, db_exists in cases:
            with self.subTest(kind=kind, readonly=readonly, header=bool(header), db_exists=db_exists):
                db = self.tmp / f"asgi-{kind}-{readonly}-{bool(header)}-{db_exists}" / "app_latest.db"
                if db_exists:
                    db.parent.mkdir(parents=True)
                    db.write_bytes(self._middle_db("KEEP").read_bytes())
                    original = db.read_bytes()
                token_file = db.parent / "health-db.token"
                if file_token:
                    token_file.parent.mkdir(parents=True, exist_ok=True)
                    token_file.write_text(file_token + "\n", encoding="utf-8")
                result = self._invoke_health_route(
                    kind=kind,
                    db=db,
                    readonly=readonly,
                    token_file=token_file if file_token else None,
                    header=header,
                )
                if not db_exists:
                    self.assertFalse(db.exists())
                    self.assertNotIn(str(db), result["body"])
                    self.assertNotIn("db_path", result["body"])
                    if header and file_token == header:
                        self.assertEqual(result["status"], 403, result["body"])
                    else:
                        self.assertEqual(result["status"], 401, result["body"])
                elif not header:
                    self.assertEqual(result["status"], 401)
                    self.assertNotIn(str(db), result["body"])
                    self.assertEqual(db.read_bytes(), original)
                else:
                    self.assertEqual(result["status"], 200, result["body"])
                    payload = json.loads(result["body"])
                    self.assertTrue(payload["read_only"])
                    self.assertNotIn("db_sha256", payload)
                    self.assertEqual(payload["fingerprint"], self._file_fingerprint(db))
                    self.assertEqual(db.read_bytes(), original)
        writable = self._invoke_health_route(
            kind="fastapi",
            db=self.tmp / "writable-missing" / "app_latest.db",
            readonly="0",
            token_file=self._write_token(self.tmp / "writable-token", token),
            header=token,
        )
        self.assertEqual(writable["status"], 403)
        self.assertNotIn("db_path", writable["body"])
        self.assertFalse((self.tmp / "writable-missing" / "app_latest.db").exists())

    def test_snapshot_thread_keeps_other_requests_responsive(self):
        db = self.tmp / "slow-snapshot" / "app_latest.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(self._middle_db("SLOW").read_bytes())
        token = secrets.token_urlsafe(16)
        token_file = self._write_token(self.tmp / "slow-snapshot" / "token", token)
        script = self.tmp / "slow-snapshot.py"
        script.write_text(
            "import asyncio, json, os, sys, threading, time\n"
            "os.environ['FAT_DISABLE_DOTENV'] = '1'\n"
            "os.environ['APP_DB_PATH'] = sys.argv[1]\n"
            "os.environ['APP_READONLY'] = '1'\n"
            "os.environ['FAT_HEALTH_DB_TOKEN_FILE'] = sys.argv[2]\n"
            "sys.path.insert(0, sys.argv[3])\n"
            "token = sys.argv[4]\n"
            "import app.db as database\n"
            "import app.health_db_asgi as gate\n"
            "import app.running_db as running_db\n"
            "from app.health_db_asgi import health_db_asgi, register_health_db\n"
            "main_id = threading.get_ident()\n"
            "seen = []\n"
            "real_connect = database.connect\n"
            "real_fp = running_db.connection_fingerprint\n"
            "real_close = gate._close_connection\n"
            "def connect_wrapped(*args, **kwargs):\n"
            "    seen.append(threading.get_ident())\n"
            "    return real_connect(*args, **kwargs)\n"
            "def close_wrapped(conn):\n"
            "    seen.append(threading.get_ident())\n"
            "    return real_close(conn)\n"
            "def fp_wrapped(conn):\n"
            "    seen.append(threading.get_ident())\n"
            "    time.sleep(0.6)\n"
            "    return real_fp(conn)\n"
            "database.connect = connect_wrapped\n"
            "gate._close_connection = close_wrapped\n"
            "running_db.connection_fingerprint = fp_wrapped\n"
            "async def measure(kind):\n"
            "    import httpx\n"
            "    from fastapi import FastAPI\n"
            "    seen.clear()\n"
            "    if kind == 'fastapi':\n"
            "        app = FastAPI()\n"
            "        @app.get('/ping')\n"
            "        async def ping():\n"
            "            return {'ok': True}\n"
            "        register_health_db(app)\n"
            "    else:\n"
            "        async def app(scope, receive, send):\n"
            "            if scope.get('path') == '/health/db':\n"
            "                await health_db_asgi(scope, receive, send)\n"
            "                return\n"
            "            body = b'{\"ok\":true}'\n"
            "            await send({'type': 'http.response.start', 'status': 200, 'headers': [\n"
            "                (b'content-type', b'application/json'),\n"
            "                (b'content-length', str(len(body)).encode()),\n"
            "            ]})\n"
            "            await send({'type': 'http.response.body', 'body': body})\n"
            "    transport = httpx.ASGITransport(app=app)\n"
            "    headers = {'X-Fat-Health-Db-Token': token}\n"
            "    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:\n"
            "        started = time.perf_counter()\n"
            "        slow_task = asyncio.create_task(client.get('/health/db', headers=headers))\n"
            "        await asyncio.sleep(0.05)\n"
            "        fast_started = time.perf_counter()\n"
            "        fast = await client.get('/ping')\n"
            "        fast_dt = time.perf_counter() - fast_started\n"
            "        denied = await client.get('/health/db')\n"
            "        denied_dt = time.perf_counter() - fast_started\n"
            "        slow = await slow_task\n"
            "        slow_dt = time.perf_counter() - started\n"
            "    return {\n"
            "        'fast_status': fast.status_code, 'fast': fast_dt,\n"
            "        'denied_status': denied.status_code, 'denied': denied_dt,\n"
            "        'slow_status': slow.status_code, 'slow': slow_dt,\n"
            "        'threads': seen, 'main': main_id,\n"
            "    }\n"
            "async def main():\n"
            "    print(json.dumps({'fastapi': await measure('fastapi'), 'asgi': await measure('asgi')}))\n"
            "asyncio.run(main())\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            ["python3", str(script), str(db), str(token_file), str(ROOT.parents[1] / "api"), token],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        for kind, item in report.items():
            with self.subTest(kind=kind):
                self.assertEqual(item["slow_status"], 200)
                self.assertEqual(item["fast_status"], 200)
                self.assertEqual(item["denied_status"], 401)
                self.assertGreaterEqual(item["slow"], 0.5)
                self.assertLess(item["fast"], 0.25)
                self.assertLess(item["denied"], 0.25)
                threads = item["threads"]
                self.assertGreaterEqual(len(threads), 3)
                self.assertEqual(len(set(threads)), 1)
                self.assertNotEqual(threads[0], item["main"])

    def test_running_api_hook_fails_closed_without_echoing_expected_hash(self):
        bait = "expected-hash-must-not-be-echoed"
        db = self.tmp / "closed.db"
        db.write_bytes(b"not-opened")
        env = os.environ.copy()
        env.update({
            "FAT_CN_DB": str(db),
            "FAT_CN_API_URL": "http://127.0.0.1:9/health/db",
            "FAT_CN_EXPECT_DB_SHA": bait,
        })
        missing = subprocess.run(
            ["python3", str(ROOT / "cn_running_api_hook.py")],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertNotIn(bait, missing.stdout)
        self.assertIn("TOKEN_MISSING", missing.stderr)
        env["FAT_HEALTH_DB_TOKEN_FILE"] = str(self._write_token(self.tmp / "closed.token", bait))
        completed = subprocess.run(
            ["python3", str(ROOT / "cn_running_api_hook.py")],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn(bait, completed.stdout)
        self.assertNotIn("running_api", completed.stdout)
        self.assertIn("API_UNREACHABLE", completed.stderr)

    def test_running_process_swap_and_stale_rollback(self):
        current = self._served_bytes("current-token")
        replacement = self._served_bytes("replacement-token")
        self.assertNotEqual(current, replacement)
        cases = (
            ("fresh", 0, replacement),
            ("stale", 13, current),
            ("disk", 16, current),
        )
        for mode, expect, remain in cases:
            with self.subTest(mode=mode):
                server, db_path, env, drop = self._initialized_package(
                    f"live-{mode}", current, replacement, "match_file",
                )
                probe = self._start_identity_probe(db_path, mode)
                try:
                    command = (
                        f"FAT_HEALTH_DB_TOKEN_FILE={probe['token_file']} "
                        f"FAT_CN_API_URL=http://127.0.0.1:{probe['port']}/health/db "
                        f"python3 {ROOT / 'cn_running_api_hook.py'}"
                    )
                    env["FAT_CN_ACTIVATE_CMD"] = command
                    env["FAT_CN_ROLLBACK_CMD"] = command
                    before = json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))["commit_id"]
                    authorized = subprocess.run(
                        ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(drop), "--ssh-target", "local"],
                        env=env, text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(authorized.returncode, 0, authorized.stdout + authorized.stderr)
                    pushed = subprocess.run(
                        ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop), "--ssh-target", "local"],
                        env=env, text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(pushed.returncode, expect, pushed.stdout + pushed.stderr)
                    self.assertEqual(db_path.read_bytes(), remain)
                    if expect == 0:
                        self.assertIn("PUSH_COMMITTED", pushed.stdout)
                        receipt = json.loads((drop / "LAST_PUSHED.json").read_text(encoding="utf-8"))
                        self.assertEqual(receipt["db_sha256"], hashlib.sha256(replacement).hexdigest())
                        self.assertTrue(receipt["activated"])
                        served = self._probe_identity(probe["port"], probe["token"])
                        self.assertEqual(served["fingerprint_source"], "running_api")
                        self.assertNotIn("db_sha256", served)
                        self.assertEqual(served["db_path"], str(db_path))
                        self.assertEqual(served["fingerprint"], self._file_fingerprint(db_path))
                    else:
                        self.assertNotIn("COMMIT_JSON", pushed.stdout)
                        self.assertNotIn("PUSH_COMMITTED", pushed.stdout)
                        self.assertEqual(
                            json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))["commit_id"],
                            before,
                        )
                        self.assertFalse((drop / "LAST_PUSHED.json").exists())
                        if mode == "stale":
                            self.assertIn("FINGERPRINT_MISMATCH", pushed.stderr)
                            self.assertNotIn("CN_PUSH_ALERT", pushed.stderr)
                            served = self._probe_identity(probe["port"], probe["token"])
                            self.assertEqual(served["fingerprint"], self._file_fingerprint(db_path))
                        else:
                            self.assertIn("FINGERPRINT_MISMATCH", pushed.stderr)
                            alert = json.loads((server / "CN_PUSH_ALERT.json").read_text(encoding="utf-8"))
                            self.assertEqual(alert["reason"], "service_rollback_unverified")
                            self.assertFalse(alert["receipt_issued"])
                finally:
                    self._stop_probe(probe)

    def test_middle_row_old_connection_does_not_commit_or_advance_base(self):
        current_path = self._middle_db("OLD!")
        replacement_path = self._middle_db("NEW!")
        self.assertEqual(self._periphery(current_path), self._periphery(replacement_path))
        self.assertNotEqual(self._file_fingerprint(current_path), self._file_fingerprint(replacement_path))
        server, db_path, env, drop = self._initialized_package(
            "middle-row", current_path.read_bytes(), replacement_path.read_bytes(), "match_file",
        )
        base_path, base_before, base_id = self._client_base(server)
        probe = self._start_identity_probe(db_path, "stale")
        try:
            self._run_push(env, drop, probe)
            failed = self._last_push
            self.assertEqual(failed.returncode, 13, failed.stdout + failed.stderr)
            self.assertNotIn("PUSH_COMMITTED", failed.stdout)
            self.assertNotIn("COMMIT_JSON", failed.stdout)
            self.assertIn("FINGERPRINT_MISMATCH", failed.stderr)
            self.assertEqual(db_path.read_bytes(), current_path.read_bytes())
            self.assertFalse((drop / "LAST_PUSHED.json").exists())
            self.assertEqual(json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))["commit_id"], base_id)
            served = self._probe_identity(probe["port"], probe["token"])
            self.assertEqual(served["fingerprint"], self._file_fingerprint(db_path))
            self.assertNotEqual(served["fingerprint"], self._file_fingerprint(replacement_path))
            self._assert_base_unchanged(base_path, base_before, base_id)
        finally:
            self._stop_probe(probe)

    def test_writable_instance_does_not_commit_or_advance_base(self):
        current = self._middle_db("LIVE")
        replacement = self._middle_db("NEXT")
        server, db_path, env, drop = self._initialized_package(
            "writable-api", current.read_bytes(), replacement.read_bytes(), "match_file",
        )
        base_path, base_before, base_id = self._client_base(server)
        missing = server / "must-not-create.db"
        probe = self._start_identity_probe(db_path, "writable")
        try:
            denied = self._probe_identity(probe["port"], probe["token"])
            self.assertEqual(denied["status"], 403)
            self.assertNotIn(str(db_path), denied["body"])
            self.assertFalse(missing.exists())
            self._run_push(env, drop, probe)
            failed = self._last_push
            self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
            self.assertNotIn("PUSH_COMMITTED", failed.stdout)
            self.assertNotIn("COMMIT_JSON", failed.stdout)
            self.assertIn("IDENTITY_REFUSED", failed.stderr)
            self.assertEqual(db_path.read_bytes(), current.read_bytes())
            self.assertFalse((drop / "LAST_PUSHED.json").exists())
            self.assertEqual(json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))["commit_id"], base_id)
            self.assertFalse((server / "must-not-create.db").exists())
            self._assert_base_unchanged(base_path, base_before, base_id)
        finally:
            self._stop_probe(probe)

    def _served_bytes(self, token: str) -> bytes:
        path = self.tmp / f"served-{token}.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE served (token TEXT)")
        conn.execute("INSERT INTO served (token) VALUES (?)", (token,))
        conn.commit()
        conn.close()
        return path.read_bytes()

    def _write_token(self, path: Path, token: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token + "\n", encoding="utf-8")
        return path

    def _middle_db(self, middle: str) -> Path:
        path = self.tmp / f"middle-{middle}.db"
        if path.exists():
            path.unlink()
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t(v TEXT)")
        conn.execute("INSERT INTO t(v) VALUES ('HEAD')")
        conn.execute("INSERT INTO t(v) VALUES (?)", (middle,))
        conn.execute("INSERT INTO t(v) VALUES ('TAIL')")
        conn.commit()
        conn.close()
        return path

    def _value_db(self, rows: list) -> Path:
        path = self.tmp / f"values-{len(list(self.tmp.glob('values-*.db')))}.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t(a, b)")
        conn.executemany("INSERT INTO t(a, b) VALUES (?, ?)", rows)
        conn.commit()
        conn.close()
        return path

    def _periphery(self, path: Path):
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            page = conn.execute("PRAGMA page_count").fetchone()[0]
            count, max_rowid = conn.execute("SELECT COUNT(*), MAX(rowid) FROM t").fetchone()
            first = conn.execute("SELECT v FROM t ORDER BY rowid ASC LIMIT 1").fetchone()[0]
            last = conn.execute("SELECT v FROM t ORDER BY rowid DESC LIMIT 1").fetchone()[0]
            return page, count, first, last, max_rowid
        finally:
            conn.close()

    def _file_fingerprint(self, path: Path) -> str:
        sys_path = str(ROOT.parents[1] / "api")
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from app.running_db import read_identity
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            return read_identity(conn)["fingerprint"]
        finally:
            conn.close()

    def _client_base(self, server: Path):
        home = server.parent / "client-home"
        record = json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))
        imported = runner.import_push_base(home, record)
        self.assertTrue(imported["ok"], imported)
        path = home / "markers" / "push_base.json"
        return path, path.read_bytes(), record["commit_id"]

    def _assert_base_unchanged(self, path: Path, before: bytes, commit_id: str) -> None:
        self.assertEqual(path.read_bytes(), before)
        runner._advance_push_base_from_commit(
            runner.home_paths(path.parents[1]), [], "ab" * 32, "cd" * 32,
        )
        record = json.loads(path.read_text(encoding="utf-8"))
        runner._advance_push_base_from_commit(
            runner.home_paths(path.parents[1]), [record], record.get("db_sha256") or "", record.get("gz_sha256") or "",
        )
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["commit_id"], commit_id)

    def _run_push(self, env: dict, drop: Path, probe: dict) -> None:
        command = (
            f"FAT_HEALTH_DB_TOKEN_FILE={probe['token_file']} "
            f"FAT_CN_API_URL=http://127.0.0.1:{probe['port']}/health/db "
            f"python3 {ROOT / 'cn_running_api_hook.py'}"
        )
        env["FAT_CN_ACTIVATE_CMD"] = command
        env["FAT_CN_ROLLBACK_CMD"] = command
        authorized = subprocess.run(
            ["bash", str(ROOT / "authorize_push_baseline.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(authorized.returncode, 0, authorized.stdout + authorized.stderr)
        self._last_push = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )

    def _invoke_health_route(self, *, kind: str, db: Path, readonly: str, token_file: Path | None, header: str) -> dict:
        script = self.tmp / f"asgi-case-{kind}-{readonly}-{db.parent.name}.py"
        script.write_text(
            "import asyncio, json, os, sys\n"
            "os.environ['FAT_DISABLE_DOTENV'] = '1'\n"
            "os.environ['APP_DB_PATH'] = sys.argv[1]\n"
            "os.environ['APP_READONLY'] = sys.argv[2]\n"
            "os.environ.pop('FAT_HEALTH_DB_TOKEN_FILE', None)\n"
            "if sys.argv[3]:\n"
            "    os.environ['FAT_HEALTH_DB_TOKEN_FILE'] = sys.argv[3]\n"
            "sys.path.insert(0, sys.argv[4])\n"
            "header = sys.argv[6]\n"
            "kind = sys.argv[5]\n"
            "async def run():\n"
            "    headers = [(b'x-fat-health-db-token', header.encode())] if header else []\n"
            "    if kind == 'fastapi':\n"
            "        import httpx\n"
            "        from fastapi import FastAPI\n"
            "        from app.health_db_asgi import register_health_db\n"
            "        app = FastAPI()\n"
            "        register_health_db(app)\n"
            "        transport = httpx.ASGITransport(app=app)\n"
            "        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:\n"
            "            response = await client.get('/health/db', headers={'X-Fat-Health-Db-Token': header} if header else {})\n"
            "        print(json.dumps({'status': response.status_code, 'body': response.text}))\n"
            "        return\n"
            "    from app.health_db_asgi import health_db_asgi\n"
            "    messages = []\n"
            "    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'GET',\n"
            "             'scheme': 'http', 'path': '/health/db', 'raw_path': b'/health/db', 'query_string': b'',\n"
            "             'headers': headers, 'client': ('203.0.113.8', 443), 'server': ('127.0.0.1', 8787)}\n"
            "    async def receive():\n"
            "        return {'type': 'http.request', 'body': b'', 'more_body': False}\n"
            "    async def send(message):\n"
            "        messages.append(message)\n"
            "    await health_db_asgi(scope, receive, send)\n"
            "    status = next(item['status'] for item in messages if item['type'] == 'http.response.start')\n"
            "    body = b''.join(item.get('body', b'') for item in messages if item['type'] == 'http.response.body')\n"
            "    print(json.dumps({'status': status, 'body': body.decode()}))\n"
            "asyncio.run(run())\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                "python3", str(script), str(db), readonly,
                str(token_file or ""), str(ROOT.parents[1] / "api"), kind, header,
            ],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return json.loads(completed.stdout)

    def _start_identity_probe(self, db_path: Path, mode: str) -> dict:
        token = secrets.token_urlsafe(16)
        token_file = self._write_token(self.tmp / f"probe-token-{mode}-{db_path.parent.name}", token)
        readonly = "1" if mode == "fresh" else "0"
        script = self.tmp / f"probe-{mode}-{db_path.parent.name}.py"
        script.write_text(
            "import hashlib, json, os, sqlite3, sys\n"
            "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
            "from pathlib import Path\n"
            "os.environ['FAT_DISABLE_DOTENV'] = '1'\n"
            "os.environ['APP_DB_PATH'] = sys.argv[1]\n"
            "os.environ['APP_READONLY'] = sys.argv[5]\n"
            "os.environ['FAT_HEALTH_DB_TOKEN_FILE'] = sys.argv[4]\n"
            "sys.path.insert(0, sys.argv[2])\n"
            "mode = sys.argv[3]\n"
            "if mode in ('fresh', 'writable'):\n"
            "    from app.health_db_asgi import invoke_health_db\n"
            "elif mode == 'stale':\n"
            "    from app.running_db import read_identity\n"
            "    held = sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + '?mode=ro', uri=True)\n"
            "    def current():\n"
            "        return read_identity(held)\n"
            "else:\n"
            "    def current():\n"
            "        raw = open(sys.argv[1], 'rb').read()\n"
            "        return {\n"
            "            'ok': True, 'read_only': True, 'fingerprint_source': 'running_api',\n"
            "            'fingerprint': hashlib.sha256(raw).hexdigest(),\n"
            "            'db_sha256': hashlib.sha256(raw).hexdigest(),\n"
            "            'db_path': sys.argv[1], 'journal_mode': 'delete',\n"
            "        }\n"
            "class Handler(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        if self.path.split('?', 1)[0] != '/health/db':\n"
            "            self.send_error(404); return\n"
            "        if mode in ('fresh', 'writable'):\n"
            "            pairs = [(key.lower().encode('latin-1'), value.encode('latin-1')) for key, value in self.headers.items()]\n"
            "            status, body = invoke_health_db(pairs)\n"
            "        else:\n"
            "            status, body = 200, json.dumps(current()).encode()\n"
            "        self.send_response(status)\n"
            "        self.send_header('Content-Type', 'application/json')\n"
            "        self.send_header('Content-Length', str(len(body)))\n"
            "        self.end_headers()\n"
            "        self.wfile.write(body)\n"
            "    def log_message(self, fmt, *args):\n"
            "        return\n"
            "server = HTTPServer(('127.0.0.1', 0), Handler)\n"
            "sys.stdout.write('PORT %s\\n' % server.server_address[1])\n"
            "sys.stdout.flush()\n"
            "server.serve_forever()\n",
            encoding="utf-8",
        )
        proc = subprocess.Popen(
            [
                "python3", str(script), str(db_path), str(ROOT.parents[1] / "api"),
                mode, str(token_file), readonly,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert proc.stdout is not None
        line = proc.stdout.readline()
        if not line.startswith("PORT "):
            err = proc.stderr.read() if proc.stderr is not None else ""
            proc.kill()
            raise AssertionError(line + err)
        return {"proc": proc, "port": int(line.split()[1]), "token": token, "token_file": str(token_file)}

    def _probe_identity(self, port: int, token: str) -> dict:
        import urllib.error
        import urllib.request
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/health/db",
            headers={"X-Fat-Health-Db-Token": token},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
                payload["status"] = response.status
                return payload
        except urllib.error.HTTPError as exc:
            return {"status": exc.code, "body": exc.read().decode("utf-8")}

    def _stop_probe(self, probe: dict) -> None:
        proc = probe["proc"]
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()

    def test_missing_live_dir_fails_closed(self):
        env_file = self.tmp / "bad.env"
        env_file.write_text(f'APP_DB_PATH="{self.db}"\n', encoding="utf-8")
        self.db.write_bytes(b"x")
        env = os.environ.copy()
        env["AUTO_CN_PUSH_ENV"] = str(env_file)
        env["AUTO_CN_PUSH_HOME"] = str(self.home)
        env.pop("ODDS_DATA_DIR", None)
        completed = subprocess.run(
            ["python3", str(ROOT / "run_auto_cn_push.py"), "--home", str(self.home), "--status"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 5, completed.stdout + completed.stderr)
        self.assertIn("ODDS_DATA_DIR", completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
