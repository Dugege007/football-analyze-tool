"""完场入库和临盘写入之后，用同一套 apply_backfill 覆盖写入 market_settle。"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "live"))

from app import db as adb  # noqa: E402
from app import market_settle as ms  # noqa: E402
import import_lib  # noqa: E402
import result_ingest as ingest  # noqa: E402

T1 = "2026-10-15T16:00:00+08:00"
T2 = "2026-10-15T17:00:00+08:00"


def _full_db(tmp_path) -> sqlite3.Connection:
    conn = adb.connect(tmp_path / "app.db")
    adb.apply_schema(conn)
    return conn


def _row(conn, match_id, book, market):
    return conn.execute(
        "SELECT * FROM market_settle WHERE match_id=? AND book=? AND market=?",
        (match_id, book, market),
    ).fetchone()


def _count(conn, match_id) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM market_settle WHERE match_id=?",
        (match_id,),
    ).fetchone()[0]


def _item(handicap: float, *, goals=(2, 1)) -> dict:
    home_goals, away_goals = goals
    return {
        "match": {
            "date": "2026-10-06",
            "weekday": "一",
            "scope": "jingcai",
            "jc": {"id": "一003", "no": "003"},
            "competition": {"name": "英超", "type": "联赛"},
            "kickoff_hour": 15,
            "kickoff_at": "2026-10-06T15:00:00+08:00",
            "teams": {"home": "主队", "away": "客队"},
        },
        "result": {
            "home_goals": home_goals,
            "away_goals": away_goals,
            "total_goals": home_goals + away_goals,
            "wdl": "胜" if home_goals > away_goals else "平" if home_goals == away_goals else "负",
        },
        "stats": {},
        "odds": {
            "asian": {
                "macau": {
                    "close": {"handicap": handicap, "home_water": 0.90, "away_water": 0.95},
                },
            },
            "euro_home_win": {"macau": {"close": 1.85}},
            "jc_home_win": {"close": 1.72},
            "jc_hhad": {
                "close": {"goal_line": "-1", "home": 3.10, "draw": 3.40, "away": 2.05},
            },
        },
        "meta": {"source_file": "test.json", "month": "2610"},
    }


def _ingest_db(tmp_path) -> str:
    folder = tmp_path / "v2d3"
    folder.mkdir()
    path = str(folder / "app.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE import_batches (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_file TEXT NOT NULL,
          month TEXT,
          imported_at TEXT NOT NULL DEFAULT (datetime('now')),
          row_count INTEGER,
          status TEXT NOT NULL DEFAULT 'ok'
        );
        CREATE TABLE matches (
          id INTEGER PRIMARY KEY,
          match_uid TEXT,
          scope TEXT,
          jingcai_date TEXT,
          jc_id TEXT,
          home_team TEXT,
          away_team TEXT,
          kickoff_at TEXT
        );
        CREATE TABLE results (
          match_id INTEGER PRIMARY KEY,
          home_goals INTEGER,
          away_goals INTEGER,
          total_goals INTEGER,
          wdl TEXT
        );
        CREATE TABLE odds_asian (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id INTEGER,
          book TEXT,
          phase TEXT,
          handicap REAL,
          home_water REAL,
          away_water REAL
        );
        INSERT INTO matches VALUES (
          1, 'a', 'jingcai', '2026-10-10', '六001', '大阪樱花', '横滨水手', '2026-10-10T13:00:00+08:00'
        );
        INSERT INTO matches VALUES (
          2, 'b', 'jingcai', '2026-10-10', '六002', '首尔FC', '济州SK', '2026-10-10T14:00:00+08:00'
        );
        INSERT INTO results VALUES (2, 9, 9, 18, '平');
        INSERT INTO odds_asian (match_id, book, phase, handicap, home_water, away_water)
        VALUES (1, 'macau', 'close', 0.25, 0.90, 0.95);
        """
    )
    conn.commit()
    conn.close()
    return path


def test_final_result_insert_writes_market_settle_and_leaves_missing_books(tmp_path):
    """完场比分新写入 results 之后，同一场立即出现十五行；缺临盘的机构保持无法获取。"""
    path = _ingest_db(tmp_path)
    now = ingest.dt.datetime(2026, 10, 14, 16, 0, tzinfo=ingest.TZ)
    decision = {
        "action": "write",
        "score": (2, 1),
        "source": "5df_csl",
        "fivedf_score": (2, 1),
        "flags": [],
    }
    # 已有比分的场次不会被覆盖，因此也不应为此重算。
    ignored = dict(decision, score=(0, 0))
    result = ingest.apply(
        path,
        [({"id": 1}, decision), ({"id": 2}, ignored)],
        tmp_path / "backup",
        "test",
        now,
    )
    assert result["inserted"] == 1
    assert result["market_settle"]["eligible_matches"] == 1
    assert result["market_settle"]["rows"] == 15

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    assert _count(conn, 1) == 15
    assert _count(conn, 2) == 0
    macau = _row(conn, 1, "macau", "ah")
    crown = _row(conn, 1, "crown", "ah")
    expected = ms.settle_asian(2, 1, 0.25)
    assert macau["cell_status"] == "ok"
    assert macau["line"] == 0.25
    assert macau["side"] == expected["side"]
    assert macau["settle_code"] == expected["settle_code"]
    assert crown["cell_status"] == "unavailable"
    assert crown["null_reason"] == "not_captured"
    assert crown["line"] is None
    assert crown["side"] is None
    # 大小球和欧盘这场都没有临盘。
    assert _row(conn, 1, "macau", "ou")["cell_status"] == "unavailable"
    assert _row(conn, 1, "macau", "x1x2")["cell_status"] == "unavailable"
    conn.close()


def test_result_ingest_second_apply_does_not_duplicate_rows(tmp_path):
    path = _ingest_db(tmp_path)
    now = ingest.dt.datetime(2026, 10, 14, 16, 0, tzinfo=ingest.TZ)
    decision = {"action": "write", "score": (2, 1), "source": "5df_csl", "flags": []}
    ingest.apply(path, [({"id": 1}, decision)], tmp_path / "backup", "test", now)
    again = ingest.apply(
        path,
        [({"id": 1}, decision)],
        tmp_path / "backup",
        "test",
        now + ingest.dt.timedelta(minutes=30),
    )
    assert again["inserted"] == 0
    assert again["market_settle"] is None
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == 15
    assert conn.execute(
        "SELECT COUNT(*) FROM market_settle WHERE match_id=1 AND book='macau' AND market='ah'"
    ).fetchone()[0] == 1
    conn.close()


def test_flags_without_a_new_score_do_not_create_market_settle(tmp_path):
    path = _ingest_db(tmp_path)
    now = ingest.dt.datetime(2026, 10, 14, 16, 0, tzinfo=ingest.TZ)
    ingest.apply(
        path,
        [({"id": 1}, {"action": "flag", "flags": [("score_conflict", {"a": 1})]})],
        tmp_path / "backup",
        "test",
        now,
    )
    conn = sqlite3.connect(path)
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_settle'"
    ).fetchone() is None
    conn.close()


def test_import_then_close_correction_overwrites_the_same_key(tmp_path):
    """月度导入写入完场和临盘后落库；临盘更正后覆盖同一行，不追加第二行。"""
    conn = _full_db(tmp_path)
    batch = conn.execute(
        "INSERT INTO import_batches (source_file, month, row_count, status) VALUES ('test.json', '2610', 1, 'ok')"
    ).lastrowid
    match_id = import_lib.insert_match(conn, _item(0.25), batch, write_prediction=False)
    conn.commit()
    first = _row(conn, match_id, "macau", "ah")
    assert first["cell_status"] == "ok"
    assert first["line"] == 0.25
    assert first["settle_code"] == ms.settle_asian(2, 1, 0.25)["settle_code"]
    assert _count(conn, match_id) == 15
    crown = _row(conn, match_id, "crown", "ah")
    assert crown["cell_status"] == "unavailable" and crown["line"] is None
    # 只有主胜的旧欧盘不能单独结算。
    euro = _row(conn, match_id, "macau", "x1x2")
    assert euro["cell_status"] == "unavailable"
    assert euro["null_reason"] == "no_close_odds"
    markets = {row[0] for row in conn.execute("SELECT DISTINCT market FROM market_settle")}
    books = {row[0] for row in conn.execute("SELECT DISTINCT book FROM market_settle")}
    assert markets == set(ms.MARKETS)
    assert books == set(ms.BOOKS)

    import_lib.insert_match(conn, _item(1.75), batch, write_prediction=False)
    conn.commit()
    second = _row(conn, match_id, "macau", "ah")
    assert second["id"] == first["id"]
    assert second["line"] == 1.75
    assert second["settle_code"] == ms.settle_asian(2, 1, 1.75)["settle_code"]
    assert _count(conn, match_id) == 15
    conn.close()


def test_totals_and_euro_close_updates_overwrite_and_stay_idempotent(tmp_path):
    conn = _full_db(tmp_path)
    cur = conn.execute(
        "INSERT INTO matches (match_uid, scope, jingcai_date, weekday, kickoff_hour, jc_id, jc_no,"
        " competition_name, competition_type, home_team, away_team, kickoff_at, kickoff_minute_known)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)",
        ("2026-10-06|一009", "jingcai", "2026-10-06", "一", 15, "一009", 9,
         "测试联赛", "联赛", "主队", "客队", "2026-10-06T15:00:00+08:00"),
    )
    match_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO results (match_id, home_goals, away_goals, total_goals, wdl) VALUES (?,?,?,?,?)",
        (match_id, 2, 1, 3, "胜"),
    )
    conn.commit()
    summary = ms.refresh_after_results_written(conn, [match_id], now=T1)
    conn.commit()
    assert summary["rows"] == 15
    assert _row(conn, match_id, "macau", "ou")["cell_status"] == "unavailable"
    assert _row(conn, match_id, "pinnacle", "x1x2")["null_reason"] == "not_captured"

    conn.execute(
        "INSERT INTO odds_snapshot (match_id, book, market, channel, point, line,"
        " water_over, water_under, stale_gap) VALUES (?,?,?,?,?,?,?,?,0)",
        (match_id, "macau", "ou", "rule", "close", 2.5, 0.88, 0.96),
    )
    conn.execute(
        "INSERT INTO odds_snapshot (match_id, book, market, channel, point,"
        " price_home, price_draw, price_away, stale_gap) VALUES (?,?,?,?,?,?,?,?,0)",
        (match_id, "pinnacle", "euro_1x2", "rule", "close", 1.70, 3.60, 5.20),
    )
    conn.commit()
    ms.refresh_after_close_writes(
        conn,
        [
            {"match_id": match_id, "market": "ou", "point": "close"},
            {"match_id": match_id, "market": "euro_1x2", "point": "close"},
            {"match_id": match_id, "market": "asian", "point": "mid"},
            {"match_id": match_id, "market": "jc_hhad", "point": "close"},
        ],
        now=T1,
    )
    conn.commit()
    ou = _row(conn, match_id, "macau", "ou")
    euro = _row(conn, match_id, "pinnacle", "x1x2")
    crown_ou = _row(conn, match_id, "crown", "ou")
    assert ou["cell_status"] == "ok"
    assert ou["line"] == 2.5
    assert ou["settle_code"] == ms.settle_totals(3, 2.5)["settle_code"]
    assert euro["cell_status"] == "ok"
    assert euro["side"] == "home"
    assert euro["odds_used"] == pytest.approx(1.70)
    assert crown_ou["cell_status"] == "unavailable"
    assert crown_ou["line"] is None

    conn.execute(
        "UPDATE odds_snapshot SET line=? WHERE match_id=? AND book='macau' AND market='ou'",
        (3.25, match_id),
    )
    conn.commit()
    ms.refresh_after_close_writes(
        conn,
        [{"match_id": match_id, "market": "goalline", "phase": "close"}],
        now=T2,
    )
    conn.commit()
    updated = _row(conn, match_id, "macau", "ou")
    assert updated["id"] == ou["id"]
    assert updated["line"] == 3.25
    assert updated["settle_code"] == ms.settle_totals(3, 3.25)["settle_code"]
    assert updated["computed_at"] == T2
    assert _count(conn, match_id) == 15

    before = [
        tuple(row)
        for row in conn.execute(
            "SELECT id, match_id, book, market, line, side, settle_code, cell_status, computed_at"
            " FROM market_settle WHERE match_id=? ORDER BY book, market",
            (match_id,),
        )
    ]
    ms.refresh_after_close_writes(
        conn,
        [{"match_id": match_id, "market": "ou", "point": "close"}],
        now=T2,
    )
    conn.commit()
    after = [
        tuple(row)
        for row in conn.execute(
            "SELECT id, match_id, book, market, line, side, settle_code, cell_status, computed_at"
            " FROM market_settle WHERE match_id=? ORDER BY book, market",
            (match_id,),
        )
    ]
    assert after == before
    conn.close()


def test_open_mid_and_jingcai_writes_do_not_create_rows(tmp_path):
    conn = _full_db(tmp_path)
    assert ms.refresh_after_close_writes(
        conn,
        [
            {"match_id": 1, "market": "asian", "point": "open"},
            {"match_id": 1, "market": "ou", "point": "mid"},
            {"match_id": 1, "market": "euro_1x2", "point": "t1"},
            {"match_id": 1, "market": "jc_spf", "point": "close"},
        ],
    ) is None
    # 建库迁移会先留下空表。初盘、中盘、真实通道和竞彩盘都不应写入机构赛果行。
    assert conn.execute("SELECT COUNT(*) FROM market_settle").fetchone()[0] == 0
    conn.close()


def test_refresh_does_not_open_another_database_or_enable_dual_write(tmp_path, monkeypatch):
    conn = _full_db(tmp_path)
    cur = conn.execute(
        "INSERT INTO matches (match_uid, scope, jingcai_date, home_team, away_team, kickoff_hour)"
        " VALUES ('2026-10-06|一010', 'jingcai', '2026-10-06', '主队', '客队', 15)"
    )
    match_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO results (match_id, home_goals, away_goals, total_goals, wdl) VALUES (?,?,?,?,?)",
        (match_id, 1, 0, 1, "胜"),
    )
    conn.commit()
    monkeypatch.delenv("DUAL_WRITE_ODDS_ASIAN", raising=False)

    def refuse_new_connection(*_args, **_kwargs):
        raise AssertionError("重算不应再打开数据库连接。")

    monkeypatch.setattr(sqlite3, "connect", refuse_new_connection)
    summary = ms.refresh_after_results_written(conn, [match_id], now=T1)
    assert summary["rows"] == 15
    assert os.environ.get("DUAL_WRITE_ODDS_ASIAN", "0") not in ("1", "true", "yes", "on")
    assert _count(conn, match_id) == 15
    conn.close()


def test_write_paths_call_the_shared_refresh():
    """钩子必须挂在完场写入和三类临盘写入或更新的函数上，而不是另写一套结算。"""
    checks = {
        REPO / "scripts/live/result_ingest.py": "refresh_after_results_written",
        REPO / "api/scripts/import_lib.py": "refresh_after_results_written",
        REPO / "scripts/live/live_capture.py": "refresh_after_close_writes",
        REPO / "scripts/live/asof_backfill_mid_rule.py": "refresh_after_close_writes",
        REPO / "api/scripts/fill_1x2_odds_snap.py": "refresh_after_close_writes",
        REPO / "api/scripts/fill_macau_mid_water.py": "refresh_after_close_writes",
        REPO / "scripts/backfill/backfill_macau_mid_water.py": "refresh_after_close_writes",
        REPO / "api/scripts/sync_odds_asian_from_snapshot.py": "refresh_after_close_writes",
        REPO / "api/scripts/resync_odds_asian_from_snapshot.py": "refresh_after_close_writes",
    }
    for path, needle in checks.items():
        assert needle in path.read_text(encoding="utf-8"), path.name
    settle_source = (ROOT / "app" / "market_settle.py").read_text(encoding="utf-8")
    assert "apply_backfill(conn, match_ids=ordered, now=now)" in settle_source
