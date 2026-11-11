"""build_result 原样返回可空红牌，缺列或未取得都是 null，不把缺数写成 0。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA_DIR, apply_schema, apply_sql_idempotent  # noqa: E402
from app.main import build_result  # noqa: E402
from app.table_matches import stored_red_cards  # noqa: E402


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_build_result_emits_null_zero_and_positive_red_cards():
    conn = _conn()
    conn.execute(
        "create table results (match_id integer primary key, home_goals integer, away_goals integer, "
        "total_goals integer, wdl text, home_red_cards integer, away_red_cards integer)")
    conn.execute("insert into results values (1, 2, 0, 2, '胜', 1, 0)")
    conn.execute("insert into results values (2, 1, 1, 2, '平', null, null)")
    confirmed = build_result(conn, 1)
    unknown = build_result(conn, 2)
    assert confirmed["home_goals"] == 2 and confirmed["away_goals"] == 0
    assert confirmed["home_red_cards"] == 1 and confirmed["away_red_cards"] == 0
    assert unknown["home_red_cards"] is None and unknown["away_red_cards"] is None
    assert build_result(conn, 9) is None


def test_build_result_missing_columns_stay_null():
    conn = _conn()
    conn.execute(
        "create table results (match_id integer primary key, home_goals integer, away_goals integer, "
        "total_goals integer, wdl text)")
    conn.execute("insert into results values (1, 3, 1, 4, '胜')")
    result = build_result(conn, 1)
    assert result["home_red_cards"] is None and result["away_red_cards"] is None
    assert result["home_goals"] == 3 and result["wdl"] == "胜"


def test_stored_red_cards_does_not_treat_missing_as_zero():
    conn = _conn()
    conn.execute("create table results (match_id integer, home_goals integer, home_red_cards integer, away_red_cards integer)")
    conn.execute("insert into results values (1, 1, 0, null)")
    row = conn.execute("select * from results").fetchone()
    assert stored_red_cards(row) == (0, None)
    assert stored_red_cards(None) == (None, None)


def test_fresh_schema_includes_nullable_red_card_columns():
    conn = _conn()
    apply_schema(conn)
    info = {row[1]: row for row in conn.execute("pragma table_info(results)")}
    assert [name for name in info if name in (
        "match_id", "home_goals", "away_goals", "total_goals", "wdl")] == [
        "match_id", "home_goals", "away_goals", "total_goals", "wdl"]
    assert info["home_red_cards"][3] == 0 and info["away_red_cards"][3] == 0


def test_red_card_migration_keeps_scores_and_is_idempotent():
    conn = _conn()
    conn.execute(
        "create table results (match_id integer primary key, home_goals integer, away_goals integer, "
        "total_goals integer, wdl text)")
    conn.execute("insert into results values (1, 2, 1, 3, '胜')")
    path = SCHEMA_DIR / "v2_2_results_red_cards.sql"
    assert apply_sql_idempotent(conn, path) == 0
    conn.commit()
    row = conn.execute(
        "select home_goals, away_goals, total_goals, wdl, home_red_cards, away_red_cards from results").fetchone()
    assert tuple(row) == (2, 1, 3, "胜", None, None)
    assert apply_sql_idempotent(conn, path) == 2
    again = conn.execute("select home_goals, away_goals, total_goals, wdl from results").fetchone()
    assert tuple(again) == (2, 1, 3, "胜")
