"""机构赛果：例表、大小球推荐写法、快照翻号、非法步进、缺临盘不顶替、欧盘三项不全、覆盖写入。"""
from __future__ import annotations

import inspect
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import avg_cells  # noqa: E402
from app import avg_conversion  # noqa: E402
from app import backtest as bt  # noqa: E402
from app import db as adb  # noqa: E402
from app import market_settle as ms  # noqa: E402
from app import table_matches as tm  # noqa: E402
from app.formal_db import BACKUP_ENV, FORMAL_DB_PATH  # noqa: E402

SCRIPT = ROOT / "scripts" / "backfill_market_settle.py"
T1 = "2026-10-15T12:00:00+08:00"
T2 = "2026-10-15T13:00:00+08:00"
ROWS = len(ms.BOOKS) * len(ms.MARKETS)


def _db(tmp_path) -> sqlite3.Connection:
    conn = adb.connect(tmp_path / "app.db")
    adb.apply_schema(conn)
    return conn


def _match(conn, uid="2020-06-06|六001", *, date="2020-06-06", kickoff="2020-06-06T15:00:00+08:00",
           hour=15, goals=(2, 1), jc_id="六001") -> int:
    cur = conn.execute(
        "INSERT INTO matches (match_uid, scope, jingcai_date, weekday, kickoff_hour, jc_id, jc_no,"
        " competition_name, competition_type, home_team, away_team, kickoff_at, kickoff_minute_known)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (uid, "jingcai", date, "六", hour, jc_id, 1, "测试联赛", "联赛", "主队", "客队", kickoff),
    )
    match_id = int(cur.lastrowid)
    if goals is not None:
        home_goals, away_goals = goals
        conn.execute(
            "INSERT INTO results (match_id, home_goals, away_goals, total_goals, wdl) VALUES (?,?,?,?,?)",
            (match_id, home_goals, away_goals, home_goals + away_goals,
             "胜" if home_goals > away_goals else "平" if home_goals == away_goals else "负"),
        )
    return match_id


def _asian(conn, match_id, book, handicap, *, home_water=0.9, away_water=0.95):
    conn.execute(
        "INSERT INTO odds_asian (match_id, book, phase, handicap, home_water, away_water) VALUES (?,?,?,?,?,?)",
        (match_id, book, "close", handicap, home_water, away_water),
    )


def _snapshot(conn, match_id, book, market, *, channel="rule", line=None, prices=(None, None, None),
              waters=(None, None), ou_waters=(None, None), source=None, recorded_at=None):
    columns = [
        "match_id", "book", "market", "channel", "point", "line",
        "price_home", "price_draw", "price_away", "water_home", "water_away", "water_over", "water_under",
    ]
    values = [match_id, book, market, channel, "close", line, *prices, *waters, *ou_waters]
    if source is not None:
        columns.append("source")
        values.append(source)
    if recorded_at is not None:
        columns.append("recorded_at")
        values.append(recorded_at)
    conn.execute(
        f"INSERT INTO odds_snapshot ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(values),
    )


def _cell(conn, match_id, book, market):
    conn.commit()
    summary = ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    row = conn.execute(
        "SELECT * FROM market_settle WHERE match_id=? AND book=? AND market=?",
        (match_id, book, market),
    ).fetchone()
    return summary, row


# ----------------------------------------------------------------------------- 纯计算


@pytest.mark.parametrize(
    ("home_goals", "away_goals", "line", "side", "push", "code", "display"),
    [
        (0, 1, 0, "away", False, "lose", "客0"),
        (1, 3, 0.75, "away", False, "lose", "客-0.75"),
        (2, 1, 0.25, "home", False, "win", "主+0.25"),
        (1, 1, 0.25, "away", False, "lose_half", "客-0.25"),
        (2, 2, 0, None, True, "push", "走水"),
        (1, 1, -0.25, "home", False, "win_half", "主-0.25"),
    ],
)
def test_asian_examples(home_goals, away_goals, line, side, push, code, display):
    cell = ms.settle_asian(home_goals, away_goals, line)
    assert cell["side"] == side
    assert cell["push"] is push
    assert cell["settle_code"] == code
    assert isinstance(cell["settle_code"], str)
    assert cell["display_text"] == display
    assert cell["cell_status"] == "ok"
    direct, _factor = bt.settle_code(home_goals, away_goals, line, "主")
    assert cell["settle_code"] == direct


def test_receiving_quarter_is_win_half_not_push():
    """比分 1-1、仓库临盘 -0.25：主队赢半，赢盘方仍是主队，不是走水。"""
    cell = ms.settle_asian(1, 1, -0.25)
    assert (cell["side"], cell["push"], cell["settle_code"], cell["display_text"]) == (
        "home", False, "win_half", "主-0.25",
    )
    # 一个半盘赢、一个半盘走水时，符号和是 1，不是 0.5。阈值只看符号和是否大于零。
    assert ms.sum_half_signs([0.5, 0.0]) == 1


def test_totals_use_home_perspective_code(monkeypatch):
    seen = []
    real = ms.bt.settle_code

    def wrap(home_goals, away_goals, line, side):
        seen.append((home_goals, away_goals, line, side))
        assert side in ("主", "客")
        return real(home_goals, away_goals, line, side)

    monkeypatch.setattr(ms.bt, "settle_code", wrap)
    win = ms.settle_totals(3, 2.25)
    half = ms.settle_totals(2, 2.25)
    push = ms.settle_totals(2, 2.0)
    assert win["side"] == "over" and win["settle_code"] == "win" and win["display_text"] == "2.25大"
    assert half["side"] == "under" and half["settle_code"] == "lose_half" and half["display_text"] == "2.25小"
    assert push["push"] is True and push["side"] is None and push["settle_code"] == "push"
    assert push["display_text"] == "走水"
    assert seen == [(3, 0, 2.25, "主"), (2, 0, 2.25, "主"), (2, 0, 2.0, "主")]
    assert all(isinstance(item["settle_code"], str) for item in (win, half, push))


def test_source_never_passes_over_as_side():
    text = Path(ms.__file__).read_text(encoding="utf-8")
    assert 'bt.settle_code(int(total_goals), 0, numeric, "主")' in text
    assert 'side="大"' not in text and "side='大'" not in text


def test_reuses_backtest_quarter_split():
    assert ms.bt.quarter_split is bt.quarter_split
    assert ms.bt.settle_code is bt.settle_code


def test_invalid_line_step_does_not_split(monkeypatch):
    def boom(_line):
        raise AssertionError("不是 0.25 步进时不能拆盘")

    monkeypatch.setattr(ms.bt, "quarter_split", boom)
    for line in (0.1, 0.2, 0.3, 1.1, -0.1):
        cell = ms.settle_asian(1, 1, line)
        assert cell["cell_status"] == "unavailable"
        assert cell["null_reason"] == "invalid_line_step"
        assert cell["side"] is None and cell["push"] is False and cell["settle_code"] is None
        assert cell["line"] == line
        totals = ms.settle_totals(2, line)
        assert totals["null_reason"] == "invalid_line_step" and totals["settle_code"] is None


def test_snapshot_line_is_negated_like_table_matches():
    for value in (0, 0.0, -0.0, 0.25, -0.25, 1.5, -1.25):
        assert ms.snapshot_asian_line_to_warehouse(value) == tm._neg(value)


def test_asian_close_prefers_warehouse_line_over_snapshot():
    quote = ms.choose_asian_close(
        {"handicap": 0.0, "home_water": 0.9, "away_water": 0.8},
        [{"channel": "rule", "market": "asian", "line": -0.75, "water_home": 1, "water_away": 1}],
    )
    assert quote["line"] == 0.0 and quote["line_source"] == "odds_asian"


def test_asian_snapshot_flip_when_warehouse_row_missing():
    quote = ms.choose_asian_close(
        None,
        [{"channel": "rule", "market": "asian", "line": 0.25, "water_home": 0.9, "water_away": 0.8}],
    )
    assert quote["line"] == -0.25 and quote["line_source"] == "odds_snapshot"
    cell = ms.settle_asian(1, 1, quote["line"])
    assert cell["side"] == "home" and cell["settle_code"] == "win_half" and cell["display_text"] == "主-0.25"


def test_euro_requires_three_prices():
    incomplete = ms.choose_euro_close(1.8, [])
    assert incomplete["euro_incomplete"] is True
    assert incomplete["odds_draw"] is None and incomplete["odds_away"] is None
    complete = ms.choose_euro_close(9.9, [{
        "channel": "rule", "market": "euro_1x2",
        "price_home": 1.11, "price_draw": 3.4, "price_away": 6.5,
    }])
    assert complete["euro_incomplete"] is False
    assert complete["odds_home"] == 1.11
    cell = ms.settle_1x2(2, 1, complete["odds_home"], complete["odds_draw"], complete["odds_away"])
    assert cell["side"] == "home" and cell["odds"] == 1.11 and cell["display_text"] == "胜 1.11"
    assert "settle_code" not in cell


def test_decimal_odds_match_frontend_fmt_num():
    assert ms.format_decimal_odds(1.11) == "1.11"
    assert ms.format_decimal_odds(1.1) == "1.1"
    assert ms.format_decimal_odds(2) == "2"


def test_prediction_settlement_is_unchanged():
    import inspect
    source = inspect.getsource(tm._settle)
    assert "legacy_asian" in source
    assert "market_settle" not in source
    assert "by_book" not in source


# ----------------------------------------------------------------------------- 落库


def test_upsert_is_idempotent_and_overwrites_same_key(tmp_path):
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(2, 1))
    _asian(conn, match_id, "macau", 0.25)
    conn.commit()
    first = ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    assert first["rows"] == ROWS and first["applied"] is True
    row = conn.execute(
        "SELECT id, settle_code, display_text, computed_at FROM market_settle WHERE match_id=? AND book='macau' AND market='ah'",
        (match_id,),
    ).fetchone()
    assert row["settle_code"] == "win" and row["display_text"] == "主+0.25" and row["computed_at"] == T1
    again = ms.apply_backfill(conn, match_ids=[match_id], now=T2)
    conn.commit()
    assert again["rows"] == ROWS
    assert conn.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == ROWS
    kept = conn.execute(
        "SELECT id, settle_code, computed_at FROM market_settle WHERE match_id=? AND book='macau' AND market='ah'",
        (match_id,),
    ).fetchone()
    assert kept["id"] == row["id"] and kept["settle_code"] == "win" and kept["computed_at"] == T2

    conn.execute("UPDATE odds_asian SET handicap=? WHERE match_id=? AND book='macau'", (1.75, match_id))
    conn.commit()
    ms.apply_backfill(conn, match_ids=[match_id], now=T2)
    conn.commit()
    updated = conn.execute(
        "SELECT id, settle_code, side, push, line FROM market_settle WHERE match_id=? AND book='macau' AND market='ah'",
        (match_id,),
    ).fetchone()
    assert updated["id"] == row["id"]
    assert updated["settle_code"] == "lose" and updated["side"] == "away" and updated["push"] == 0
    assert updated["line"] == 1.75
    assert conn.execute("SELECT COUNT(*) FROM market_settle WHERE match_id=?", (match_id,)).fetchone()[0] == ROWS


def test_missing_close_does_not_borrow_another_book(tmp_path):
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(2, 1))
    _asian(conn, match_id, "crown", 0.25)
    conn.commit()
    ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    macau = conn.execute(
        "SELECT * FROM market_settle WHERE match_id=? AND book='macau' AND market='ah'",
        (match_id,),
    ).fetchone()
    crown = conn.execute(
        "SELECT * FROM market_settle WHERE match_id=? AND book='crown' AND market='ah'",
        (match_id,),
    ).fetchone()
    assert macau["cell_status"] == "unavailable" and macau["null_reason"] == "not_captured"
    assert macau["side"] is None and macau["line"] is None and macau["settle_code"] is None
    assert crown["cell_status"] == "ok" and crown["line"] == 0.25 and crown["settle_code"] == "win"
    assert crown["line_source"] == "odds_asian"
    avg = conn.execute(
        "SELECT * FROM market_settle WHERE match_id=? AND book='avg' AND market='ah'",
        (match_id,),
    ).fetchone()
    assert avg["cell_status"] == "unavailable" and avg["null_reason"] == "not_captured"
    assert avg["line"] is None and avg["side"] is None and avg["settle_code"] is None


def test_snapshot_flip_is_stored_and_invalid_step_is_not_scored(tmp_path):
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(1, 1))
    # 快照 0.25 是接口记法（主队受让 0.25），仓库记法为 -0.25。
    _snapshot(conn, match_id, "pinnacle", "asian", line=0.25, waters=(0.88, 0.98))
    _snapshot(conn, match_id, "william", "ou", line=0.3, ou_waters=(0.9, 0.9))
    _snapshot(conn, match_id, "bet365", "ou", line=2.25, ou_waters=(0.91, 0.93))
    conn.commit()
    ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    asian = conn.execute(
        "SELECT * FROM market_settle WHERE match_id=? AND book='pinnacle' AND market='ah'",
        (match_id,),
    ).fetchone()
    assert asian["line"] == -0.25 and asian["line_source"] == "odds_snapshot"
    assert asian["side"] == "home" and asian["settle_code"] == "win_half" and asian["display_text"] == "主-0.25"
    assert isinstance(asian["settle_code"], str)
    bad = conn.execute(
        "SELECT * FROM market_settle WHERE match_id=? AND book='william' AND market='ou'",
        (match_id,),
    ).fetchone()
    assert bad["cell_status"] == "unavailable" and bad["null_reason"] == "invalid_line_step"
    assert bad["settle_code"] is None and bad["side"] is None and bad["push"] == 0
    good = conn.execute(
        "SELECT * FROM market_settle WHERE match_id=? AND book='bet365' AND market='ou'",
        (match_id,),
    ).fetchone()
    assert good["side"] == "under" and good["settle_code"] == "lose_half" and good["display_text"] == "2.25小"
    direct, _factor = bt.settle_code(2, 0, 2.25, "主")
    assert good["settle_code"] == direct


def test_euro_home_only_is_not_settled(tmp_path):
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(2, 0))
    conn.execute(
        "INSERT INTO odds_euro_home (match_id, book, phase, home_win) VALUES (?,?,?,?)",
        (match_id, "macau", "close", 1.8),
    )
    _snapshot(conn, match_id, "crown", "euro_1x2", prices=(1.11, None, 6.5))
    _snapshot(conn, match_id, "pinnacle", "euro_1x2", prices=(1.11, 3.4, 6.5))
    conn.commit()
    ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    for book in ("macau", "crown"):
        row = conn.execute(
            "SELECT * FROM market_settle WHERE match_id=? AND book=? AND market='x1x2'",
            (match_id, book),
        ).fetchone()
        assert row["cell_status"] == "unavailable" and row["null_reason"] == "no_close_odds"
        assert row["side"] is None and row["settle_code"] is None and row["odds_used"] is None
    ok = conn.execute(
        "SELECT * FROM market_settle WHERE match_id=? AND book='pinnacle' AND market='x1x2'",
        (match_id,),
    ).fetchone()
    assert ok["side"] == "home" and ok["odds_used"] == 1.11 and ok["display_text"] == "胜 1.11"
    assert ok["settle_code"] is None and ok["cell_status"] == "ok"


def test_score_withdrawn_or_special_status_deletes_rows(tmp_path):
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(2, 1))
    _asian(conn, match_id, "macau", 0)
    conn.commit()
    ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == ROWS
    conn.execute(
        "CREATE TABLE result_ingest_flags (match_id INTEGER NOT NULL, flag TEXT NOT NULL, detail_json TEXT,"
        " first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, resolved_at TEXT, PRIMARY KEY (match_id, flag))"
    )
    conn.execute(
        "INSERT INTO result_ingest_flags VALUES (?, 'refund', '{}', 't', 't', NULL)",
        (match_id,),
    )
    conn.commit()
    ms.apply_backfill(conn, match_ids=[match_id], now=T2)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == 0

    conn.execute("DELETE FROM result_ingest_flags")
    ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == ROWS
    conn.execute("UPDATE results SET home_goals=NULL, away_goals=NULL, total_goals=NULL, wdl=NULL WHERE match_id=?", (match_id,))
    conn.commit()
    ms.apply_backfill(conn, match_ids=[match_id], now=T2)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == 0


def test_extra_time_unverified_is_not_scored(tmp_path):
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(2, 1))
    _asian(conn, match_id, "macau", 0.25)
    conn.execute(
        "CREATE TABLE result_ingest_flags (match_id INTEGER NOT NULL, flag TEXT NOT NULL, detail_json TEXT,"
        " first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, resolved_at TEXT, PRIMARY KEY (match_id, flag))"
    )
    conn.execute(
        "INSERT INTO result_ingest_flags VALUES (?, 'extra_time_unverified', '{}', 't', 't', NULL)",
        (match_id,),
    )
    conn.commit()
    summary = ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    assert summary["eligible_matches"] == 0
    assert conn.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == 0


def test_other_channel_is_not_used_as_close(tmp_path):
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(2, 1))
    _snapshot(conn, match_id, "macau", "asian", channel="actual", line=-0.25)
    conn.commit()
    ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    row = conn.execute(
        "SELECT cell_status, null_reason, line FROM market_settle WHERE book='macau' AND market='ah'",
    ).fetchone()
    assert row["cell_status"] == "unavailable" and row["null_reason"] == "not_captured" and row["line"] is None


# ----------------------------------------------------------------------------- 批量接口


def _flags(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS result_ingest_flags (match_id INTEGER NOT NULL, flag TEXT NOT NULL,"
        " detail_json TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, resolved_at TEXT,"
        " PRIMARY KEY (match_id, flag))"
    )


def test_table_by_book_shape_and_unfinished_is_null(tmp_path):
    conn = _db(tmp_path)
    finished = _match(conn, goals=(2, 1))
    _asian(conn, finished, "macau", 0.25)
    _snapshot(conn, finished, "macau", "ou", line=2.25, ou_waters=(0.9, 0.9))
    _snapshot(conn, finished, "macau", "euro_1x2", prices=(1.11, 3.4, 6.5))
    future = _match(
        conn, uid="2099-01-02|五001", date="2099-01-02", kickoff="2099-01-02T20:00:00+08:00",
        hour=20, goals=(3, 0), jc_id="五001",
    )
    _asian(conn, future, "macau", 0.5)
    _flags(conn)
    refund = _match(
        conn, uid="2020-06-06|六002", date="2020-06-06", kickoff="2020-06-06T18:00:00+08:00",
        hour=18, goals=None, jc_id="六002",
    )
    conn.execute("INSERT INTO result_ingest_flags VALUES (?, 'refund', '{}', 't', 't', NULL)", (refund,))
    conn.commit()

    payload = tm.build_table(
        conn, date_from="2020-06-06", date_to="2020-06-06", scope="all", strategy="CFFXDJ_5_V3",
        channel="rule", include_live="none", settlement_version=None, as_of=None,
        baseline_window_days=None, baseline_min_n=20, limit=50, offset=0,
    )
    # 现算不能趁机写库。
    assert conn.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == 0
    by_uid = {item["match_id"]: item for item in payload["items"]}
    done = by_uid["2020-06-06|六001"]["result"]
    assert done["outcome_1x2"] == "home"
    assert done["home_goals"] == 2 and done["away_goals"] == 1
    macau = done["by_book"]["macau"]
    assert macau["ah"]["settle_code"] == "win" and macau["ah"]["display_text"] == "主+0.25"
    assert macau["ah"]["cell_status"] == "ok" and macau["ah"]["side"] == "home" and macau["ah"]["push"] is False
    assert macau["ou"]["display_text"] == "2.25大" and macau["ou"]["settle_code"] == "win"
    assert macau["x1x2"]["display_text"] == "胜 1.11" and macau["x1x2"]["odds"] == 1.11
    assert "settle_code" not in macau["x1x2"]
    # 皇冠没有临盘，不能拿澳门的盘口顶上。
    assert done["by_book"]["crown"]["ah"]["cell_status"] == "unavailable"
    assert done["by_book"]["crown"]["ah"]["null_reason"] == "not_captured"
    assert done["by_book"]["crown"]["ah"]["line"] is None
    assert set(done["by_book"]) == set(ms.BOOKS)
    assert set(done["by_book"]["avg"]) == {"ah", "ou", "x1x2"}
    assert set(done["by_book"]["avg"]["ah"]) == set(macau["ah"])
    assert set(done["by_book"]["avg"]["x1x2"]) == set(macau["x1x2"])
    # 展示列用的快照没有来源和记录时刻时，多家平均不能拿澳门临盘顶上。
    assert done["by_book"]["avg"]["ah"]["cell_status"] == "unavailable"
    assert done["by_book"]["avg"]["ah"]["null_reason"] == "not_captured"
    assert done["by_book"]["avg"]["ah"]["line"] is None
    assert done["by_book"]["avg"]["ou"]["line"] is None
    assert done["by_book"]["avg"]["x1x2"]["side"] is None
    void = by_uid["2020-06-06|六002"]["result"]
    assert void["status"] == "refund" and void["by_book"] is None and void["outcome_1x2"] is None

    hidden = tm.build_table(
        conn, date_from="2099-01-02", date_to="2099-01-02", scope="all", strategy="CFFXDJ_5_V3",
        channel="rule", include_live="none", settlement_version=None, as_of="2026-10-15T12:00:00+08:00",
        baseline_window_days=None, baseline_min_n=20, limit=50, offset=0,
    )
    future_row = hidden["items"][0]
    assert future_row["result"] is None
    tm.TableResponse.model_validate(payload)
    blocked = tm._market_by_book(finished, {}, {finished: {"macau": {}}}, {finished})
    assert blocked["macau"]["ah"]["null_reason"] == "extra_time_unverified"
    assert blocked["macau"]["ah"]["settle_code"] is None and blocked["crown"]["ou"]["side"] is None

    # 表里有行时接口读表，不再按临盘重算。
    ms.apply_backfill(conn, match_ids=[finished], now=T1)
    conn.execute(
        "UPDATE market_settle SET display_text=?, settle_code=?, side=?, push=0 WHERE match_id=? AND book='macau' AND market='ah'",
        ("表内原文", "push", None, finished),
    )
    conn.commit()
    reread = tm.build_table(
        conn, date_from="2020-06-06", date_to="2020-06-06", scope="jc", strategy="CFFXDJ_5_V3",
        channel="rule", include_live="none", settlement_version=None, as_of=None,
        baseline_window_days=None, baseline_min_n=20, limit=50, offset=0,
    )
    stored = next(item for item in reread["items"] if item["match_id"] == "2020-06-06|六001")
    assert stored["result"]["by_book"]["macau"]["ah"]["display_text"] == "表内原文"
    assert stored["result"]["by_book"]["macau"]["ah"]["settle_code"] == "push"
    assert stored["settlement"] is None or stored["settlement"]["code"] != "表内原文"

    flat = tm.build_table(
        conn, date_from="2020-06-06", date_to="2099-01-02", scope="all", strategy="CFFXDJ_5_V3",
        channel="rule", include_live="none", settlement_version=None, as_of="2026-10-15T12:00:00+08:00",
        baseline_window_days=None, baseline_min_n=20, limit=50, offset=0, fmt="flat",
    )
    keysets = {tuple(sorted(item.keys())) for item in flat["items"]}
    assert len(keysets) == 1
    assert "result_by_book_macau_ah_settle_code" in flat["items"][0]
    assert "result_by_book_macau_ah_display_text" in flat["items"][0]
    assert "result_by_book_avg_ah_cell_status" in flat["items"][0]
    assert "result_by_book_avg_ou_display_text" in flat["items"][0]
    assert "result_by_book_avg_x1x2_odds" in flat["items"][0]
    assert "result_outcome_1x2" in flat["items"][0]


def test_attach_market_result_nulls_whole_by_book_when_not_finished():
    unfinished = tm.attach_market_result(
        {"status": "refund", "home_goals": None}, "refund", None, None,
        finished=False, outcome="home", by_book={"macau": {}},
    )
    assert unfinished["by_book"] is None and unfinished["outcome_1x2"] is None
    assert tm.attach_market_result(None, "before_kickoff", None, None, finished=False, outcome=None, by_book={}) is None


# ----------------------------------------------------------------------------- 多家平均


AVG_RECORDED = "2020-06-06T14:00:00+08:00"


def _avg_close(conn, match_id, book, market, **kwargs):
    """写入一条能进入多家平均临盘的规则快照。来源和记录时刻缺一，展示列就不会采用。"""
    _snapshot(
        conn, match_id, book, market,
        source="5df_live", recorded_at=AVG_RECORDED, **kwargs,
    )


def _display_avg_close(conn, match_id):
    """用与数据表相同的 build_tables 读取 close，供结算结果对照。"""
    rows = conn.execute(
        "SELECT * FROM odds_snapshot WHERE match_id = ? AND channel = 'rule' AND point = 'close'",
        (match_id,),
    ).fetchall()
    bag = type("Bag", (), {"snap_by_match": {match_id: list(rows)}, "timeline": {}})()
    kickoff = datetime.fromisoformat("2020-06-06T15:00:00+08:00")
    return avg_cells.build_tables(bag, match_id, kickoff, {}, datetime.fromisoformat(T1), {})["avg"]


def test_by_book_template_includes_avg_with_the_same_shape():
    blank = ms.blank_by_book()
    assert set(blank) == set(ms.BOOKS)
    assert ms.AVG_BOOK in blank
    assert set(blank["avg"]) == set(blank["macau"]) == set(ms.MARKETS)
    assert set(blank["avg"]["ah"]) == set(blank["macau"]["ah"])
    assert set(blank["avg"]["ou"]) == set(blank["macau"]["ou"])
    assert set(blank["avg"]["x1x2"]) == set(blank["macau"]["x1x2"])
    filled = ms.ensure_by_book_keys({
        "macau": {"ah": {"side": "home", "display_text": "主+0.25", "cell_status": "ok"}},
    })
    assert filled["avg"]["ah"]["cell_status"] is None
    assert filled["avg"]["ou"]["push"] is False
    assert filled["avg"]["x1x2"]["odds"] is None
    assert filled["macau"]["ah"]["side"] == "home"
    assert "snapshot_asian_line_to_warehouse" not in inspect.getsource(ms._quote_from_avg_cell)
    pending = ms._quote_from_avg_cell("ah", {
        "cell_status": "pending", "cell_status_reason": "not_due", "line": 0.25,
    })
    assert pending["blocked_reason"] == "not_captured" and pending["line"] is None
    too_few = ms._quote_from_avg_cell("ou", {
        "cell_status": "insufficient", "cell_status_reason": "too_few_books", "line": 2.25,
    })
    assert too_few["blocked_reason"] == "too_few_books" and too_few["line"] is None
    assert too_few["line_source"] == "avg_close"


def test_avg_close_matches_display_column_and_does_not_flip_again(tmp_path):
    """快照亚盘 -0.25 表示主队让球 0.25。多家平均落库必须仍是 +0.25，并且不得抄澳门仓库临盘。"""
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(2, 1))
    _asian(conn, match_id, "macau", 1.75)
    for book, home_price in (("macau", 1.50), ("crown", 1.70)):
        _avg_close(conn, match_id, book, "asian", line=-0.25, waters=(0.92, 0.88))
        _avg_close(conn, match_id, book, "ou", line=2.25, ou_waters=(0.90, 0.95))
        draw_price = 3.40 if book == "macau" else 3.60
        away_price = 6.00 if book == "macau" else 5.00
        _avg_close(
            conn, match_id, book, "euro_1x2",
            prices=(home_price, draw_price, away_price),
        )
    conn.commit()

    preview = tm.build_table(
        conn, date_from="2020-06-06", date_to="2020-06-06", scope="all", strategy="CFFXDJ_5_V3",
        channel="rule", include_live="none", settlement_version=None, as_of=None,
        baseline_window_days=None, baseline_min_n=20, limit=50, offset=0,
    )
    assert conn.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == 0
    shown = preview["items"][0]["result"]["by_book"]["avg"]
    assert shown["ah"]["line"] == 0.25
    assert shown["ah"]["display_text"] == "主+0.25"
    assert shown["ah"]["settle_code"] == "win"
    assert shown["ou"]["line"] == 2.25 and shown["ou"]["display_text"] == "2.25大"
    assert shown["x1x2"]["side"] == "home"
    assert shown["x1x2"]["odds"] != 1.50
    assert set(shown["ah"]) == set(preview["items"][0]["result"]["by_book"]["macau"]["ah"])

    ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    display = _display_avg_close(conn, match_id)
    ah = conn.execute(
        "SELECT * FROM market_settle WHERE match_id=? AND book='avg' AND market='ah'",
        (match_id,),
    ).fetchone()
    assert display["ah"]["close"]["line"] == 0.25
    assert ah["line"] == display["ah"]["close"]["line"]
    assert ah["line_source"] == "avg_close"
    settled = ms.settle_asian(2, 1, ah["line"])
    assert ah["side"] == settled["side"] == "home"
    assert ah["push"] == 0
    assert ah["settle_code"] == settled["settle_code"] == "win"
    assert isinstance(ah["settle_code"], str)
    assert ah["display_text"] == "主+0.25"
    macau = conn.execute(
        "SELECT line, settle_code FROM market_settle WHERE book='macau' AND market='ah'",
    ).fetchone()
    assert macau["line"] == 1.75 and macau["settle_code"] == "lose"
    assert ah["line"] != macau["line"]

    ou = conn.execute(
        "SELECT * FROM market_settle WHERE book='avg' AND market='ou'",
    ).fetchone()
    assert ou["line"] == display["ou"]["close"]["line"] == 2.25
    ou_settled = ms.settle_totals(3, ou["line"])
    assert ou["side"] == ou_settled["side"] == "over"
    assert ou["settle_code"] == ou_settled["settle_code"]
    assert ou["display_text"] == "2.25大"

    euro = conn.execute(
        "SELECT * FROM market_settle WHERE book='avg' AND market='x1x2'",
    ).fetchone()
    close_euro = display["x1x2"]["close"]
    assert euro["odds_home"] == close_euro["home"]
    assert euro["odds_draw"] == close_euro["draw"]
    assert euro["odds_away"] == close_euro["away"]
    euro_settled = ms.settle_1x2(2, 1, euro["odds_home"], euro["odds_draw"], euro["odds_away"])
    assert euro["side"] == "home" and euro["settle_code"] is None
    assert euro["odds_used"] == euro_settled["odds"] == close_euro["home"]
    assert euro["display_text"] == euro_settled["display_text"]
    assert euro["odds_used"] != 1.50
    assert conn.execute(
        "SELECT COUNT(*) FROM market_settle WHERE book='avg'",
    ).fetchone()[0] == len(ms.MARKETS)


def test_avg_receiving_quarter_line_is_not_negated_again(tmp_path):
    """快照 +0.25 经多家平均后是仓库记法 -0.25。比分 1-1 为主队赢半，短文案是主-0.25。"""
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(1, 1))
    for book in ("pinnacle", "bet365"):
        _avg_close(conn, match_id, book, "asian", line=0.25, waters=(0.90, 0.90))
    conn.commit()
    ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    row = conn.execute(
        "SELECT * FROM market_settle WHERE book='avg' AND market='ah'",
    ).fetchone()
    direct = ms.settle_asian(1, 1, -0.25)
    assert row["line"] == -0.25
    assert row["side"] == direct["side"] == "home"
    assert row["settle_code"] == direct["settle_code"] == "win_half"
    assert row["display_text"] == direct["display_text"] == "主-0.25"
    assert row["push"] == 0 and row["cell_status"] == "ok"


def test_avg_too_few_books_stays_unavailable_and_does_not_copy_william(tmp_path):
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(2, 1))
    _asian(conn, match_id, "william", 0.0)
    _avg_close(conn, match_id, "william", "asian", line=-0.5, waters=(0.91, 0.91))
    _avg_close(conn, match_id, "william", "ou", line=2.5, ou_waters=(0.88, 0.96))
    _avg_close(conn, match_id, "william", "euro_1x2", prices=(1.80, 3.40, 4.20))
    conn.commit()
    assert avg_conversion.MIN_BOOKS == 2
    ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    for market in ms.MARKETS:
        row = conn.execute(
            "SELECT * FROM market_settle WHERE book='avg' AND market=?",
            (market,),
        ).fetchone()
        assert row["cell_status"] == "unavailable"
        assert row["null_reason"] == "too_few_books"
        assert row["null_reason_zh"] == "参与平均的机构少于两家"
        assert row["side"] is None and row["settle_code"] is None and row["display_text"] is None
        assert row["line"] is None and row["odds_used"] is None and row["push"] == 0
    william = conn.execute(
        "SELECT line, cell_status, settle_code FROM market_settle WHERE book='william' AND market='ou'",
    ).fetchone()
    assert william["cell_status"] == "ok" and william["line"] == 2.5 and william["settle_code"] == "win"
    assert conn.execute("SELECT COUNT(*) FROM market_settle WHERE book='avg'").fetchone()[0] == 3


def test_api_computes_missing_avg_rows_without_writing_or_replacing_stored_books(tmp_path):
    """五家机构已经落库、多家平均三行还没有时，接口现算平均赛果，不覆盖原行，也不把结果写回数据库。"""
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(2, 1))
    _asian(conn, match_id, "macau", 1.75)
    for book in ("macau", "crown"):
        _avg_close(conn, match_id, book, "asian", line=-0.25, waters=(0.92, 0.88))
    conn.commit()
    ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.execute(
        "UPDATE market_settle SET display_text=?, settle_code=?, side=? WHERE book='macau' AND market='ah'",
        ("表内原文", "push", None),
    )
    conn.execute("DELETE FROM market_settle WHERE book='avg'")
    conn.commit()
    payload = tm.build_table(
        conn, date_from="2020-06-06", date_to="2020-06-06", scope="all", strategy="CFFXDJ_5_V3",
        channel="rule", include_live="none", settlement_version=None, as_of=None,
        baseline_window_days=None, baseline_min_n=20, limit=50, offset=0,
    )
    assert conn.execute("SELECT COUNT(*) FROM market_settle WHERE book='avg'").fetchone()[0] == 0
    item = next(row for row in payload["items"] if row["match_id"] == "2020-06-06|六001")
    assert item["result"]["by_book"]["macau"]["ah"]["display_text"] == "表内原文"
    assert item["result"]["by_book"]["macau"]["ah"]["settle_code"] == "push"
    avg = item["result"]["by_book"]["avg"]["ah"]
    assert avg["line"] == 0.25 and avg["display_text"] == "主+0.25" and avg["settle_code"] == "win"
    assert avg["cell_status"] == "ok"
    assert set(avg) == set(item["result"]["by_book"]["macau"]["ah"])


def test_finished_match_does_not_keep_pending_average(tmp_path):
    """比分已经入库，但开赛时刻仍晚于本次计算时刻时，多家平均写成无法获取，而不是未到点。"""
    conn = _db(tmp_path)
    match_id = _match(
        conn, uid="2099-06-06|六009", date="2099-06-06", kickoff="2099-06-06T15:00:00+08:00",
        hour=15, goals=(1, 0), jc_id="六009",
    )
    conn.commit()
    ms.apply_backfill(conn, match_ids=[match_id], now=T1)
    conn.commit()
    row = conn.execute(
        "SELECT cell_status, null_reason, side, line FROM market_settle WHERE book='avg' AND market='ah'",
    ).fetchone()
    assert row["cell_status"] == "unavailable"
    assert row["null_reason"] == "not_captured"
    assert row["side"] is None and row["line"] is None


def test_avg_only_backfill_does_not_remove_other_books(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(2, 1))
    _asian(conn, match_id, "macau", 0.25)
    for book in ("macau", "crown"):
        _avg_close(conn, match_id, book, "asian", line=-0.25, waters=(0.92, 0.88))
    conn.commit()

    def refuse_average(*_args, **_kwargs):
        raise AssertionError("只回填一家机构时不应计算多家平均。")

    real_build = avg_cells.build_tables
    monkeypatch.setattr(ms.avg_cells, "build_tables", refuse_average)
    macau_only = ms.apply_backfill(conn, match_ids=[match_id], now=T1, books=["macau"])
    conn.commit()
    assert macau_only["rows"] == len(ms.MARKETS)
    assert macau_only["books"] == ["macau"]
    assert conn.execute("SELECT COUNT(*) FROM market_settle WHERE book='avg'").fetchone()[0] == 0
    macau_row = conn.execute(
        "SELECT id, line FROM market_settle WHERE book='macau' AND market='ah'",
    ).fetchone()
    assert macau_row["line"] == 0.25

    monkeypatch.setattr(ms.avg_cells, "build_tables", real_build)
    avg_only = ms.apply_backfill(conn, match_ids=[match_id], now=T2, books=["avg"])
    conn.commit()
    assert avg_only["rows"] == len(ms.MARKETS)
    assert avg_only["books"] == ["avg"]
    assert conn.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == 6
    kept = conn.execute(
        "SELECT id, line FROM market_settle WHERE book='macau' AND market='ah'",
    ).fetchone()
    assert kept["id"] == macau_row["id"] and kept["line"] == 0.25
    avg = conn.execute(
        "SELECT line, settle_code, display_text, cell_status FROM market_settle WHERE book='avg' AND market='ah'",
    ).fetchone()
    assert avg["line"] == 0.25 and avg["settle_code"] == "win" and avg["display_text"] == "主+0.25"
    assert avg["cell_status"] == "ok"
    with pytest.raises(ValueError, match="不认识的机构代号"):
        ms.plan_backfill(conn, books=["nope"])


# ----------------------------------------------------------------------------- 脚本默认不写库


def _run_script(args, env=None):
    merged = os.environ.copy()
    merged["FAT_DISABLE_DOTENV"] = "1"
    merged.pop(BACKUP_ENV, None)
    if env:
        merged.update(env)
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=merged)


def test_script_requires_db_and_dry_run_does_not_write(tmp_path):
    missing = _run_script([])
    assert missing.returncode == 2

    conn = _db(tmp_path)
    match_id = _match(conn, goals=(1, 0))
    _asian(conn, match_id, "macau", 0)
    conn.commit()
    conn.execute("DROP TABLE market_settle")
    conn.commit()
    conn.close()
    path = str(tmp_path / "app.db")
    dry = _run_script(["--db", path])
    assert dry.returncode == 0, dry.stderr
    body = json.loads(dry.stdout)
    assert body["dry_run"] is True and body["applied"] is False and body["eligible_matches"] == 1
    check = sqlite3.connect(path)
    names = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "market_settle" not in names
    check.close()

    applied = _run_script(["--db", path, "--apply"])
    assert applied.returncode == 0, applied.stderr
    check = sqlite3.connect(path)
    assert check.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == ROWS
    check.close()


def test_script_avg_backfill_writes_three_rows_and_rejects_unknown_book(tmp_path):
    conn = _db(tmp_path)
    match_id = _match(conn, goals=(1, 1))
    _asian(conn, match_id, "macau", 0)
    for book in ("macau", "crown"):
        _avg_close(conn, match_id, book, "asian", line=0.25, waters=(0.90, 0.90))
    conn.commit()
    conn.execute("DROP TABLE market_settle")
    conn.commit()
    conn.close()
    path = str(tmp_path / "app.db")

    dry = _run_script(["--db", path, "--book", "avg"])
    assert dry.returncode == 0, dry.stderr
    body = json.loads(dry.stdout)
    assert body["dry_run"] is True and body["applied"] is False
    assert body["rows"] == 3 and body["books"] == ["avg"]
    check = sqlite3.connect(path)
    names = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "market_settle" not in names
    check.close()

    five = _run_script(["--db", path, "--apply", "--book", "macau"])
    assert five.returncode == 0, five.stderr
    check = sqlite3.connect(path)
    assert check.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == 3
    macau_id = check.execute(
        "SELECT id FROM market_settle WHERE book='macau' AND market='ah'",
    ).fetchone()[0]
    check.close()

    applied = _run_script(["--db", path, "--apply", "--book", "avg"])
    assert applied.returncode == 0, applied.stderr
    check = sqlite3.connect(path)
    check.row_factory = sqlite3.Row
    assert check.execute("SELECT COUNT(*) FROM market_settle WHERE book='avg'").fetchone()[0] == 3
    assert check.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == 6
    assert check.execute(
        "SELECT id FROM market_settle WHERE book='macau' AND market='ah'",
    ).fetchone()[0] == macau_id
    avg = check.execute(
        "SELECT * FROM market_settle WHERE book='avg' AND market='ah'",
    ).fetchone()
    assert avg["line"] == -0.25
    assert avg["side"] == "home" and avg["settle_code"] == "win_half"
    assert avg["display_text"] == "主-0.25"
    assert avg["cell_status"] == "ok"
    missing_market = check.execute(
        "SELECT cell_status, null_reason FROM market_settle WHERE book='avg' AND market='ou'",
    ).fetchone()
    assert missing_market["cell_status"] == "unavailable"
    assert missing_market["null_reason"] == "not_captured"
    check.close()

    unknown = _run_script(["--db", path, "--book", "nope"])
    assert unknown.returncode == 2
    assert "不认识的机构代号" in unknown.stderr


def test_script_refuses_formal_database_without_backup_ack():
    refused = _run_script(["--db", str(FORMAL_DB_PATH), "--apply"])
    assert refused.returncode != 0
    assert "备份" in refused.stderr
