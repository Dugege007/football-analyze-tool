"""赛事待确认标记。待定池合入前：有赛事来源名且 league_id 为空返回 true，没有来源名返回 false。

不写正式库，不建待定池表。列表和详情走 build_match_obj，数据表走 build_table。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import league_pending as lp  # noqa: E402
from app import team_pending as tp  # noqa: E402
from app.main import build_match_obj  # noqa: E402


def test_rule_source_name_and_empty_id():
    assert lp.is_pending(None, "英超") is True
    assert lp.is_pending("", "英超") is True
    assert lp.is_pending("  ", "英超") is True
    assert lp.is_pending(None, "  西甲  ") is True


def test_rule_id_present_is_false():
    assert lp.is_pending(7, "英超") is False
    assert lp.is_pending("7", "英超") is False
    assert lp.is_pending(0, "英超") is False  # 数字 0 不是空编号


def test_rule_no_source_name_is_false():
    assert lp.is_pending(None, None) is False
    assert lp.is_pending(None, "") is False
    assert lp.is_pending(None, "   ") is False
    assert lp.is_pending(7, None) is False
    assert lp.is_pending(7, "") is False


def test_from_row_uses_league_id_and_competition_name():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (competition_name TEXT, league_id INTEGER)")
    conn.execute("INSERT INTO t VALUES (?, ?)", ("英超", None))
    conn.execute("INSERT INTO t VALUES (?, ?)", ("西甲", 3))
    conn.execute("INSERT INTO t VALUES (?, ?)", (None, None))
    rows = conn.execute("SELECT * FROM t").fetchall()
    assert [lp.from_row(r) for r in rows] == [True, False, False]
    conn.execute("CREATE TABLE bare (competition_name TEXT)")
    conn.execute("INSERT INTO bare VALUES (?)", ("英超",))
    bare = conn.execute("SELECT * FROM bare").fetchone()
    assert lp.from_row(bare) is True  # 没有 league_id 列视为编号为空


def _match_row(conn, *, name, league_id, uid="u1"):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS matches (
            jc_id TEXT, jc_no INTEGER, weekday TEXT, jingcai_date TEXT, scope TEXT,
            competition_name TEXT, competition_type TEXT, competition_stage TEXT,
            kickoff_hour INTEGER, kickoff_at TEXT, kickoff_minute_known INTEGER,
            home_team TEXT, away_team TEXT, home_team_id INTEGER, away_team_id INTEGER,
            league_id INTEGER
        )"""
    )
    conn.execute("DELETE FROM matches")
    conn.execute(
        """INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("周六001", 1, "六", "2026-10-17", "jingcai", name, "联赛", None,
         20, "2026-10-17T20:00:00+08:00", 1, "主队", "客队", None, None, league_id),
    )
    return conn.execute("SELECT * FROM matches").fetchone()


def test_list_detail_match_object_carries_flag():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    pending = tp.NameIndex(set(), set(), [])
    named = build_match_obj(_match_row(conn, name="英超", league_id=None), {}, pending)
    assert named["league_pending_confirm"] is True
    assert named["competition"]["name"] == "英超"
    assert named["competition"]["league_pending_confirm"] is True
    linked = build_match_obj(_match_row(conn, name="英超", league_id=4), {}, pending)
    assert linked["league_pending_confirm"] is False
    assert linked["competition"]["league_pending_confirm"] is False
    blank = build_match_obj(_match_row(conn, name=None, league_id=None), {}, pending)
    assert blank["league_pending_confirm"] is False
    assert blank["competition"]["league_pending_confirm"] is False
    assert blank["competition"]["name"] is None


def _table_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE matches (
          id INTEGER PRIMARY KEY,
          match_uid TEXT, scope TEXT, jingcai_date TEXT, weekday TEXT,
          kickoff_hour INTEGER, jc_id TEXT, jc_no INTEGER,
          competition_name TEXT, competition_type TEXT,
          home_team TEXT, away_team TEXT,
          kickoff_at TEXT, kickoff_minute_known INTEGER,
          league_id INTEGER, home_team_id INTEGER, away_team_id INTEGER
        );
        CREATE TABLE leagues (id INTEGER PRIMARY KEY, name_zh_canonical TEXT);
        CREATE TABLE teams (id INTEGER PRIMARY KEY, name_zh_canonical TEXT);
        CREATE TABLE match_meta (match_id INTEGER, extras_json TEXT);
        CREATE TABLE predictions (match_id INTEGER, strategy TEXT);
        CREATE TABLE results (match_id INTEGER, home_goals INTEGER, away_goals INTEGER, total_goals INTEGER, wdl TEXT);
        CREATE TABLE odds_asian (
          match_id INTEGER, book TEXT, phase TEXT, handicap REAL,
          home_water REAL, away_water REAL, water_src TEXT,
          water_censored INTEGER, extras_json TEXT
        );
        CREATE TABLE odds_euro_home (match_id INTEGER, book TEXT, phase TEXT, home_win REAL);
        CREATE TABLE odds_jc_home (match_id INTEGER, phase TEXT, home_win REAL);
        """
    )
    conn.execute("INSERT INTO leagues (id, name_zh_canonical) VALUES (1, '西班牙甲级联赛')")
    rows = [
        ("2099-01-02|named-empty", "英超", None),
        ("2099-01-02|named-linked", "西甲", 1),
        ("2099-01-02|no-name", None, None),
        ("2099-01-02|blank-name", "  ", None),
    ]
    for uid, name, league_id in rows:
        conn.execute(
            """INSERT INTO matches (
                 match_uid, scope, jingcai_date, weekday, kickoff_hour, jc_id, jc_no,
                 competition_name, competition_type, home_team, away_team,
                 kickoff_at, kickoff_minute_known, league_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, "jingcai", "2099-01-02", "五", 20, None, None,
             name, "联赛", "主队", "客队", "2099-01-02T20:00:00+08:00", 1, league_id),
        )
    conn.commit()
    return conn


def test_table_matches_nested_and_flat():
    from app import table_matches as tm

    conn = _table_db()
    common = dict(
        date_from="2099-01-02", date_to="2099-01-02", scope="all", strategy="CFFXDJ_5_V3",
        channel="rule", include_live="none", settlement_version=None, as_of=None,
        baseline_window_days=None, baseline_min_n=20, limit=20, offset=0,
    )
    nested = tm.build_table(conn, fmt="nested", **common)
    by_id = {item["match_id"]: item["match"] for item in nested["items"]}
    assert by_id["2099-01-02|named-empty"]["league"] == "英超"
    assert by_id["2099-01-02|named-empty"]["league_canonical"] is None
    assert by_id["2099-01-02|named-empty"]["league_pending_confirm"] is True
    linked = by_id["2099-01-02|named-linked"]
    assert linked["league"] == "西甲"
    assert linked["league_canonical"] == "西班牙甲级联赛"
    assert linked["league_pending_confirm"] is False
    assert by_id["2099-01-02|no-name"]["league"] is None
    assert by_id["2099-01-02|no-name"]["league_pending_confirm"] is False
    assert by_id["2099-01-02|blank-name"]["league_pending_confirm"] is False
    for match in by_id.values():
        tm.MatchInfo.model_validate(match)

    flat = tm.build_table(conn, fmt="flat", **common)
    flat_by_id = {row["match_id"]: row for row in flat["items"]}
    assert flat_by_id["2099-01-02|named-empty"]["league_pending_confirm"] is True
    assert flat_by_id["2099-01-02|named-linked"]["league_pending_confirm"] is False
    assert flat_by_id["2099-01-02|no-name"]["league_pending_confirm"] is False
    keysets = {tuple(sorted(row.keys())) for row in flat["items"]}
    assert len(keysets) == 1
    conn.close()
