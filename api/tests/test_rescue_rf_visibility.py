"""R-F 事后补写在决策时刻不可当作即时可见盘。特征只收截断后仍合法的水位。"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import table_matches as tm  # noqa: E402

T = datetime.fromisoformat("2026-10-12T23:30:00+08:00")
KICK = datetime.fromisoformat("2026-10-13T00:30:00+08:00")
BEFORE = "2026-10-12T23:20:00+08:00"
AFTER = "2026-10-12T23:40:00+08:00"


def _extras(*, features_ok=True):
    return {
        "source": "5df_hist_asof",
        "capture": "asof_hist",
        "asof_backfill": True,
        "phase_assign_late": True,
        "features_ok": features_ok,
        "sim_ok": True,
        "rescue_rule": "R-F",
        "recommend_live_ok": False,
        "channel": "actual",
        "point": "t1",
    }


def test_posthoc_gate_blocks_future_tick_and_live_hit():
    ex = _extras()
    assert tm.is_posthoc_rescue(ex, source="5df_hist_asof")
    assert tm.posthoc_counts_live_hit(ex, source="5df_hist_asof") is False
    assert tm.posthoc_feature_blocked(ex, datetime.fromisoformat(AFTER), T, source="5df_hist_asof")
    assert tm.posthoc_feature_blocked(ex, datetime.fromisoformat(BEFORE), T, source="5df_hist_asof") is False
    assert tm.posthoc_feature_blocked(ex, T, T, source="5df_hist_asof") is False
    # 非补写行不因报价时刻被这条规则拦住
    assert tm.posthoc_feature_blocked({}, datetime.fromisoformat(AFTER), T, source="5df_live") is False
    assert tm.posthoc_counts_live_hit({}, source="5df_live") is True
    assert tm.posthoc_counts_live_hit({"phase_assign_late": True}, source="5df_live") is False


def _db(tmp_path: Path, *, recorded_at: str, line: float, book: str = "crown") -> sqlite3.Connection:
    db = tmp_path / "app.db"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE odds_asian (
          match_id INTEGER, book TEXT, phase TEXT, handicap REAL,
          home_water REAL, away_water REAL, water_src TEXT,
          water_censored INTEGER, extras_json TEXT
        );
        CREATE TABLE odds_euro_home (
          match_id INTEGER, book TEXT, phase TEXT, home_win REAL
        );
        CREATE TABLE odds_jc_home (
          match_id INTEGER, phase TEXT, home_win REAL
        );
        CREATE TABLE odds_snapshot (
          id INTEGER PRIMARY KEY,
          match_id INTEGER, book TEXT, market TEXT, channel TEXT, point TEXT,
          recorded_at TEXT, target_at TEXT, line REAL,
          water_home REAL, water_away REAL, water_censored INTEGER,
          water_src TEXT, source TEXT, extras_json TEXT
        );
        """
    )
    c.execute(
        "INSERT INTO odds_snapshot (id, match_id, book, market, channel, point, recorded_at, target_at,"
        " line, water_home, water_away, water_src, source, extras_json)"
        " VALUES (1, 1, ?, 'asian', 'actual', 't1', ?, ?, ?, 0.91, 0.95, 'actual', '5df_hist_asof', ?)",
        (book, recorded_at, "2026-10-12T23:30:00+08:00", line, json.dumps(_extras(), ensure_ascii=False)),
    )
    c.commit()
    return c


def test_legal_asof_water_enters_features_but_not_live_hit(tmp_path):
    c = _db(tmp_path, recorded_at=BEFORE, line=-0.5)
    try:
        d = tm._Data(c, [1])
        cell = tm.build_ah_cell(d, 1, "crown", "close_real", {}, T, KICK)
    finally:
        c.close()
    assert cell["available"] is True
    assert cell["line"] == 0.5  # API 负主让 → 接口正主让
    assert cell["features_ok"] is True
    assert cell["phase_assign_late"] is True
    assert cell["asof_backfill"] is True
    assert cell["home_water"] == 0.91


def test_tick_after_decision_time_is_hidden(tmp_path):
    c = _db(tmp_path, recorded_at=AFTER, line=-1.5)
    try:
        d = tm._Data(c, [1])
        cell = tm.build_ah_cell(d, 1, "crown", "close_real", {}, T, KICK)
    finally:
        c.close()
    assert cell["available"] is False
    assert cell["hidden_reason"] == "after_as_of"
    assert cell["features_ok"] is False
    assert cell["sim_ok"] is False
    assert cell["phase_assign_late"] is True
    assert cell["asof_backfill"] is True
    assert cell["line"] is None
    assert cell["home_water"] is None


def test_macau_rescue_stays_off_manual_column(tmp_path):
    c = _db(tmp_path, recorded_at=BEFORE, line=-0.5, book="macau")
    try:
        d = tm._Data(c, [1])
        manual = tm.build_ah_cell(d, 1, "macau", "close_real", {}, T, KICK)
        five = tm.build_macau_5df_cell(d, 1, "close_real", {}, T, KICK)
    finally:
        c.close()
    assert manual["available"] is False
    assert five["available"] is True
    assert five["phase_assign_late"] is True
    assert five["features_ok"] is True
    assert five["asof_backfill"] is True
    assert five["book_lane"] == "macau_5df"
