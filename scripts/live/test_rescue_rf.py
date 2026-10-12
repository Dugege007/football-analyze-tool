"""R-F：真实通道 Strict as-of。不调 5DF，不写现网库。"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import asof_backfill_mid_rule as ab  # noqa: E402
import rescue_queue as rq  # noqa: E402
import rescue_worker as rw  # noqa: E402

TZ = ab.TZ
T_ISO = "2026-10-12T23:30:00+08:00"
KICK_ISO = "2026-10-13T00:30:00+08:00"
TK = "2026-10-12|一002|close|real"


def _real_item(**over) -> dict:
    """与待消费队列条目同形：真实临盘 actual/t1。"""
    it = {
        "schema": "live_rescue_queue_v1",
        "target_key": TK,
        "jingcai_date": "2026-10-12",
        "match_uid": "2026-10-12|一002",
        "fixture_id": 421776248,
        "channel": "actual",
        "point": "t1",
        "phase_variant": "real",
        "target_at": T_ISO,
        "kickoff_at": KICK_ISO,
        "enqueued_at": "2026-10-12T23:53:20+08:00",
        "reason": "missed_after_window",
        "scenario": "S1",
        "priority": 993,
        "attempts": 0,
        "max_attempts": 5,
        "status": "pending",
        "rescue_plan": ["R-C", "R-F"],
        "expires_mode": "kickoff_plus_3h_recommend_live_off",
        "notes": "",
    }
    it.update(over)
    return it


def test_prematch_asof_rejects_tick_after_T():
    """recorded_at > T 不得入选；恰等于 T 的赛前 tick 可以。"""
    target = datetime.fromisoformat(T_ISO)
    kick = datetime.fromisoformat(KICK_ISO)
    ticks = [
        {"recorded_at": "2026-10-12T22:00:00+08:00", "line": -0.25,
         "home": 1.90, "away": 1.90, "minute": None, "suspended": False},
        {"recorded_at": T_ISO, "line": -0.5,
         "home": 1.91, "away": 1.95, "minute": None, "suspended": False},
        {"recorded_at": "2026-10-12T23:31:00+08:00", "line": -1.0,
         "home": 1.80, "away": 1.80, "minute": None, "suspended": False},
    ]
    best = ab.prematch_asof(ticks, target, kick)
    assert best is not None and best["line"] == -0.5
    assert ab.parse(best["recorded_at"]) <= target

    only_future = [ticks[2]]
    assert ab.prematch_asof(only_future, target, kick) is None

    # 更晚但已是赛中 / 封盘的 tick 不能压过赛前有效盘
    blocked = [
        ticks[0],
        {"recorded_at": T_ISO, "line": -2.0, "home": 1.90, "away": 1.90,
         "minute": 1, "suspended": False},
        {"recorded_at": "2026-10-12T23:29:00+08:00", "line": -3.0, "home": 1.90, "away": 1.90,
         "minute": None, "suspended": True},
    ]
    kept = ab.prematch_asof(blocked, target, kick)
    assert kept["line"] == -0.25


def test_real_pending_dry_run_is_not_deferred():
    now = datetime(2026, 10, 12, 23, 53, tzinfo=TZ)
    got = rw.process_one(Path("/tmp/does-not-need-to-exist.json"), _real_item(),
                         db=Path("/tmp/unused.db"), dry_run=True, now=now)
    assert got["action"] == "would_asof"
    assert got["rescue_rule"] == "R-F"
    assert got["recommend_live_ok"] is True
    assert "defer" not in got["action"]


def test_real_pending_after_kickoff_plus_3h_is_late_but_still_rf():
    now = datetime(2026, 10, 13, 4, 0, tzinfo=TZ)  # 开赛 00:30 之后超过 3 小时
    got = rw.process_one(Path("/tmp/does-not-need-to-exist.json"), _real_item(),
                         db=Path("/tmp/unused.db"), dry_run=True, now=now)
    assert got["action"] == "would_asof_late"
    assert got["rescue_rule"] == "R-F"
    assert got["recommend_live_ok"] is False


def test_rule_item_still_uses_ra_or_rd():
    now = datetime(2026, 10, 12, 12, 0, tzinfo=TZ)
    rule = {
        "target_key": "2026-10-12|一002|close|rule",
        "jingcai_date": "2026-10-12",
        "channel": "rule",
        "point": "close",
        "phase_variant": "rule",
        "kickoff_at": KICK_ISO,
    }
    got = rw.process_one(Path("/tmp/rule.json"), rule, db=Path("/tmp/unused.db"),
                         dry_run=True, now=now)
    assert got["rescue_rule"] == "R-A"
    assert got["action"] == "would_asof"


def _isolate(monkeypatch, tmp_path):
    live = tmp_path / "live"
    for name, dest in (
        ("D_PLAN", live / "plan"),
        ("D_STATE", live / "state"),
        ("LOG_DIR", live / "logs"),
        ("RAW_DIR", live / "raw"),
        ("D_BAK", live / "bak"),
    ):
        monkeypatch.setattr(ab, name, dest)
    q = tmp_path / "q"
    for name in ("PENDING", "DONE", "FAILED", "SHADOW"):
        monkeypatch.setattr(rq, name, q / name.lower())
    monkeypatch.setattr(rq, "QLOG", q / "logs" / "enqueue.jsonl")
    monkeypatch.setattr(rw, "QLOG", q / "logs" / "consume.jsonl")
    monkeypatch.delenv("DUAL_WRITE_ODDS_ASIAN", raising=False)


def _schema(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.executescript(
        """
        CREATE TABLE matches (
          id INTEGER PRIMARY KEY,
          match_uid TEXT UNIQUE,
          home_team TEXT,
          away_team TEXT,
          home_team_id INTEGER,
          away_team_id INTEGER
        );
        CREATE TABLE odds_snapshot (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id INTEGER NOT NULL,
          book TEXT NOT NULL,
          market TEXT NOT NULL,
          channel TEXT NOT NULL,
          point TEXT NOT NULL,
          recorded_at TEXT,
          target_at TEXT,
          lag_hours REAL,
          stale_gap INTEGER NOT NULL DEFAULT 0,
          line REAL,
          price_home REAL,
          price_away REAL,
          price_draw REAL,
          price_over REAL,
          price_under REAL,
          water_home REAL,
          water_away REAL,
          water_over REAL,
          water_under REAL,
          water_src TEXT,
          water_censored INTEGER,
          source TEXT,
          extras_json TEXT,
          UNIQUE (match_id, book, market, channel, point)
        );
        """
    )
    c.execute(
        "INSERT INTO matches (id, match_uid, home_team, away_team, home_team_id, away_team_id)"
        " VALUES (7, '2026-10-12|一002', '主队', '客队', 1, 2)"
    )
    c.commit()
    c.close()


def _plan(tmp_path) -> None:
    plan = {
        "jingcai_date": "2026-10-12",
        "matches": [{
            "match_uid": "2026-10-12|一002",
            "fixture_id": 421776248,
            "home": "主队",
            "away": "客队",
        }],
        "targets": [{
            "target_key": TK,
            "match_uid": "2026-10-12|一002",
            "fixture_id": 421776248,
            "phase": "close",
            "phase_variant": "real",
            "channel": "actual",
            "point": "t1",
            "target_at": T_ISO,
            "kickoff_at": KICK_ISO,
        }],
    }
    p = ab.D_PLAN / "2026-10-12.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


class _FakeHist:
    def __init__(self):
        self.calls = 0
        self.remaining = 40


def _ticks():
    return [
        {"recorded_at": "2026-10-12T22:00:00+08:00", "line": -0.25,
         "home": 1.90, "away": 1.90, "minute": None, "suspended": False},
        {"recorded_at": T_ISO, "line": -0.5,
         "home": 1.91, "away": 1.95, "minute": None, "suspended": False},
        {"recorded_at": "2026-10-12T23:40:00+08:00", "line": -1.5,
         "home": 1.70, "away": 1.70, "minute": None, "suspended": False},
    ]


def _patch_hist(monkeypatch):
    monkeypatch.setattr(ab, "HistClient", _FakeHist)

    def _fetch(cl, fid, slug, use_cache=True):
        return "ok", _ticks(), {"path": "mem", "cache": True}

    monkeypatch.setattr(ab, "fetch_hist", _fetch)


def test_rf_write_actual_cell_flags_and_skips_tick_after_T(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    db = tmp_path / "v2d3" / "app.db"
    _schema(db)
    _plan(tmp_path)
    _patch_hist(monkeypatch)
    rep = ab.run("2026-10-12", [TK], False, db, False, False,
                 recommend_live_ok=False, rescue_rule="R-F")
    trep = rep["targets"][0]
    assert trep["channel"] == "actual" and trep["point"] == "t1"
    assert trep["rescue_rule"] == "R-F"
    assert trep["recommend_live_ok"] is False
    assert trep["target_status"] == "asof_backfilled"
    assert all(b["line"] == -0.5 for b in trep["books"].values())
    assert all(b["result"] == "inserted" for b in trep["books"].values())

    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM odds_snapshot").fetchall()
    assert rows and all(r["channel"] == "actual" and r["point"] == "t1" for r in rows)
    assert all(r["source"] == "5df_hist_asof" for r in rows)
    assert c.execute("SELECT COUNT(*) FROM odds_snapshot WHERE channel='rule'").fetchone()[0] == 0
    for r in rows:
        assert r["line"] == -0.5
        assert r["recorded_at"] == T_ISO
        ex = json.loads(r["extras_json"])
        assert ex["asof_backfill"] is True
        assert ex["phase_assign_late"] is True
        assert ex["rescue_rule"] == "R-F"
        assert ex["features_ok"] is True
        assert ex["sim_ok"] is True
        assert ex["recommend_live_ok"] is False
        assert ex["channel"] == "actual" and ex["point"] == "t1"
    c.close()


def test_rf_does_not_overwrite_live_or_manual(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    db = tmp_path / "v2d3" / "app.db"
    _schema(db)
    _plan(tmp_path)
    _patch_hist(monkeypatch)
    c = sqlite3.connect(db)
    c.execute(
        "INSERT INTO odds_snapshot (match_id, book, market, channel, point, line, source)"
        " VALUES (7, 'macau', 'asian', 'actual', 't1', -9.0, '5df_live')"
    )
    c.execute(
        "INSERT INTO odds_snapshot (match_id, book, market, channel, point, line, source)"
        " VALUES (7, 'crown', 'asian', 'actual', 't1', -8.0, 'manual_v3')"
    )
    c.commit()
    c.close()
    rep = ab.run("2026-10-12", [TK], False, db, False, False,
                 recommend_live_ok=True, rescue_rule="R-F")
    books = rep["targets"][0]["books"]
    assert books["macau"]["result"] == "keep_existing_live"
    assert books["crown"]["result"] == "conflict_other_source"
    assert books["william"]["result"] == "inserted"
    c = sqlite3.connect(db)
    assert c.execute("SELECT line FROM odds_snapshot WHERE book='macau'").fetchone()[0] == -9.0
    assert c.execute("SELECT line FROM odds_snapshot WHERE book='crown'").fetchone()[0] == -8.0
    line, source, extras = c.execute(
        "SELECT line, source, extras_json FROM odds_snapshot WHERE book='william'"
    ).fetchone()
    assert (line, source) == (-0.5, "5df_hist_asof")
    ex = json.loads(extras)
    assert ex["asof_backfill"] is True and ex["phase_assign_late"] is True
    assert ex["rescue_rule"] == "R-F" and ex["recommend_live_ok"] is True
    assert ex["features_ok"] is True and ex["sim_ok"] is True
    c.close()


def test_dual_write_still_refused(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    db = tmp_path / "v2d3" / "app.db"
    db.parent.mkdir(parents=True)
    monkeypatch.setenv("DUAL_WRITE_ODDS_ASIAN", "1")
    with pytest.raises(SystemExit, match="DUAL_WRITE"):
        ab.run("2026-10-12", [TK], False, db, True, False, rescue_rule="R-F")


def test_worker_executes_real_item_and_marks_late_flags(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    captured = {}

    def _fake_run(D, target_key, db, dry_run, recommend_live_ok=None, rescue_rule=None):
        captured.update(date=D, target_key=target_key, recommend_live_ok=recommend_live_ok,
                        rescue_rule=rescue_rule, dry_run=dry_run)
        return {
            "targets": [{"target_key": target_key, "target_status": "asof_backfilled"}],
            "report_path": "mem.json",
        }

    monkeypatch.setattr(rw, "run_asof", _fake_run)
    path = tmp_path / "2026-10-12__一002__t1__real__abcd.json"
    path.write_text(json.dumps(_real_item(), ensure_ascii=False), encoding="utf-8")
    now = datetime(2026, 10, 13, 4, 0, tzinfo=TZ)
    got = rw.process_one(path, json.loads(path.read_text(encoding="utf-8")),
                         db=tmp_path / "v2d3" / "app.db", dry_run=False, now=now)
    assert got["action"] == "asof_backfilled_late"
    assert got["rescue_rule"] == "R-F"
    assert captured["rescue_rule"] == "R-F"
    assert captured["recommend_live_ok"] is False
    assert captured["target_key"] == TK
    assert not path.exists()
    done = list(rq.DONE.glob("*.json"))
    assert len(done) == 1
    moved = json.loads(done[0].read_text(encoding="utf-8"))
    assert moved["status"] == "asof_backfilled"
    assert moved["rescue_rule"] == "R-F"
    assert moved["recommend_live_ok"] is False
    assert moved["sim_ok"] is True and moved["features_ok"] is True
    late = json.loads((rq.SHADOW / f"{path.stem}__late_report.json").read_text(encoding="utf-8"))
    assert late["rescue_rule"] == "R-F"
    assert late["recommend_live_ok"] is False
    assert late["sim_ok"] is True and late["features_ok"] is True
    assert "defer" not in json.dumps(got, ensure_ascii=False)
