#!/usr/bin/env python3
"""临时库、假钟和假推送上的保护测试。不连接生产，不推送。"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import signal
import sqlite3
import subprocess
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
from prepare_formal_package import build_package  # noqa: E402

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


def kill_tree(pid: int) -> None:
    children = Path(f"/proc/{pid}/task/{pid}/children")
    if children.is_file():
        for token in children.read_text(encoding="utf-8", errors="replace").split():
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
        lock_path.parent.mkdir()
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
        import gzip as gzip_mod
        import hashlib
        import io

        def pack(raw: bytes, mtime: int) -> bytes:
            buf = io.BytesIO()
            with gzip_mod.GzipFile(fileobj=buf, mode="wb", mtime=mtime) as handle:
                handle.write(raw)
            return buf.getvalue()

        def stage(directory: Path, raw: bytes, mtime: int) -> str:
            blob = pack(raw, mtime)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "app_latest.db.gz").write_bytes(blob)
            (directory / "app_latest.db.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii")
            gz_sha = hashlib.sha256(blob).hexdigest()
            (directory / "app_latest.db.gz.sha256").write_text(gz_sha + "\n", encoding="ascii")
            (directory / "meta.json").write_text(json.dumps({"jc_day": "2026-11-20"}), encoding="utf-8")
            (directory / "app_latest.ready").write_text(json.dumps({
                "db_sha256": hashlib.sha256(raw).hexdigest(),
                "gz_sha256": gz_sha,
            }), encoding="utf-8")
            return gz_sha

        make_db(self.db, historical_days())
        write_json(self.home / "markers" / "pushed_2026-11-11.json", {"jc_day": "2026-11-11", "db_sha256": "seed"})
        prepared = runner.prepare_unpushed(home=self.home, db=self.db, live_root=self.live)
        pending = prepared["prepared"][0]
        staging = self.home / "staging"
        new_blob = (staging / "app_latest.db.gz").read_bytes()
        raw = gzip_mod.decompress(new_blob)
        self.assertEqual(hashlib.sha256(new_blob).hexdigest(), pending["gz_sha256"])
        old_buf = io.BytesIO()
        with gzip_mod.GzipFile(fileobj=old_buf, mode="wb", mtime=11) as handle:
            handle.write(raw)
        old_blob = old_buf.getvalue()
        old_gz = hashlib.sha256(old_blob).hexdigest()
        self.assertNotEqual(old_gz, pending["gz_sha256"])
        old_body = json.dumps({
            "source": "msi",
            "jc_day": pending["jc_day"],
            "db_sha256": pending["db_sha256"],
            "gz_sha256": old_gz,
        }).encode()
        server = self.tmp / "server"
        server.mkdir()
        (server / "app_latest.db").write_bytes(raw)
        drop = self.tmp / "drop-new-gz"
        drop.mkdir()
        for name in ("app_latest.db.gz", "app_latest.db.sha256", "app_latest.db.gz.sha256", "meta.json"):
            (drop / name).write_bytes((staging / name).read_bytes())
        (drop / "app_latest.ready").write_text(json.dumps({
            "db_sha256": pending["db_sha256"],
            "gz_sha256": pending["gz_sha256"],
        }), encoding="utf-8")
        (drop / "LAST_PUSHED.json").write_bytes(old_body)
        health = self.tmp / "health.sh"
        health.write_text("#!/bin/sh\nprintf '%s' '{\"ok\":true}'\n", encoding="utf-8")
        health.chmod(0o755)
        ssh_bin = self.tmp / "bin"
        ssh_bin.mkdir()
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
        env = os.environ.copy()
        env.update({
            "PATH": str(ssh_bin) + os.pathsep + env.get("PATH", ""),
            "FAT_CN_ROOT": str(server),
            "FAT_CN_LOCK": str(self.tmp / "remote.lock"),
            "FAT_CN_HEALTH_CMD": str(health),
        })
        done = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("PUSH_COMMITTED", done.stdout)
        self.assertIn("fat-cn-push.", done.stderr)
        self.assertNotIn("fat-finish-replace.sh", done.stderr)
        self.assertEqual((drop / "LAST_PUSHED.json.previous").read_bytes(), old_body)
        committed = json.loads((drop / "LAST_PUSHED.json").read_text(encoding="utf-8"))
        self.assertEqual(committed["gz_sha256"], pending["gz_sha256"])
        self.assertEqual(committed["db_sha256"], pending["db_sha256"])
        self.assertTrue(committed["health_ok"] is True)
        self.assertTrue(str(committed["workdir"]).find("/fat-cn-push.") > 0)
        self.assertFalse(Path(committed["workdir"]).exists())
        self.assertEqual((server / "app_latest.db").read_bytes(), raw)
        self.assertFalse((drop / "app_latest.ready").exists())
        server_commit = json.loads((server / "LAST_COMMIT.json").read_text(encoding="utf-8"))
        self.assertEqual(server_commit["commit_id"], committed["commit_id"])
        marked = runner.mark_confirmed(
            home=self.home, jc_day=pending["jc_day"], db=self.db, live_root=self.live,
            receipts=[committed],
        )
        self.assertTrue(marked["ok"], marked)
        self.assertFalse((self.home / "markers" / "pending_push.json").exists())
        self.assertTrue((self.home / "markers" / "pushed_2026-11-12.json").is_file())
        self.assertEqual((drop / "LAST_PUSHED.json.previous").read_bytes(), old_body)
        newer = b"formal-db-bytes-v2"
        (server / "app_latest.db").write_bytes(newer)
        late = self.tmp / "drop-late"
        stage(late, raw, 2)
        late_run = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(late), "--ssh-target", "local"],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(late_run.returncode, 12, late_run.stdout + late_run.stderr)
        self.assertIn("STALE_PREDECESSOR", late_run.stderr)
        self.assertEqual((server / "app_latest.db").read_bytes(), newer)
        self.assertFalse((late / "LAST_PUSHED.json").exists())
        waiter = self.tmp / "drop-wait"
        stage(waiter, newer, 3)
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
            stage(rival, newer, 4)
            blocked = subprocess.run(
                ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(rival), "--ssh-target", "local"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(blocked.returncode, 9, blocked.stdout + blocked.stderr)
            self.assertIn("REMOTE_PUSH_LOCK_HELD", blocked.stderr)
            self.assertTrue((rival / "app_latest.ready").is_file())
            self.assertFalse((rival / "LAST_PUSHED.json").exists())
            same = subprocess.run(
                ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(waiter)],
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

    def test_health_failure_restores_db(self):
        import hashlib
        server = self.tmp / "server-health"
        server.mkdir()
        (server / "app_latest.db").write_bytes(b"current-db")
        drop = self.tmp / "drop-health"
        blob = gzip.compress(b"replacement-db", mtime=0)
        drop.mkdir()
        (drop / "app_latest.db.gz").write_bytes(blob)
        (drop / "app_latest.db.sha256").write_text(hashlib.sha256(b"replacement-db").hexdigest() + "\n", encoding="ascii")
        gz_sha = hashlib.sha256(blob).hexdigest()
        (drop / "app_latest.db.gz.sha256").write_text(gz_sha + "\n", encoding="ascii")
        (drop / "meta.json").write_text(json.dumps({"jc_day": "2026-11-20"}), encoding="utf-8")
        (drop / "app_latest.ready").write_text(json.dumps({
            "db_sha256": hashlib.sha256(b"replacement-db").hexdigest(),
            "gz_sha256": gz_sha,
        }), encoding="utf-8")
        (drop / "LAST_PUSHED.json").write_text(json.dumps({
            "db_sha256": hashlib.sha256(b"current-db").hexdigest(),
            "gz_sha256": "c" * 64,
        }), encoding="utf-8")
        health = self.tmp / "nested-ok.sh"
        health.write_text("#!/bin/sh\nprintf '%s' '{\"meta\":{\"ok\":true}}'\n", encoding="utf-8")
        health.chmod(0o755)
        env = os.environ.copy()
        env.update({"FAT_CN_ROOT": str(server), "FAT_CN_LOCK": str(self.tmp / "health.lock"), "FAT_CN_HEALTH_CMD": str(health)})
        failed = subprocess.run(
            ["bash", str(ROOT / "invoke_cn_push.sh"), "--dir", str(drop)],
            env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(failed.returncode, 13, failed.stdout + failed.stderr)
        self.assertIn("remote health failed", failed.stderr)
        self.assertEqual((server / "app_latest.db").read_bytes(), b"current-db")
        self.assertFalse((server / "LAST_COMMIT.json").exists())
        self.assertTrue((drop / "app_latest.ready").is_file())
        self.assertIn('"gz_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"', (drop / "LAST_PUSHED.json").read_text(encoding="utf-8"))

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
