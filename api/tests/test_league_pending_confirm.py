"""赛事待确认与球队共用常用名升格规则。

英超、英格兰超级联赛已经升格，league_id 为空也不标星。
临时名和名册外的赛事名继续标星。赛事编号不能单独取消星号。
外部球队定稿缺失不得把已升格的名称改成全员待确认。
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


def test_known_league_is_false_without_id():
    idx = tp.load_league_index()
    assert idx.authoritative is True
    assert lp.is_pending(None, "英超", idx) is False
    assert lp.is_pending(None, "英格兰超级联赛", idx) is False
    assert lp.is_pending("", "  意大利甲级联赛  ", idx) is False
    assert lp.is_pending(None, "日本J联赛", idx) is False
    assert lp.is_pending(None, "德国甲级联赛", idx) is False
    assert lp.is_pending(28, "英格兰超级联赛", idx) is False


def test_unmapped_league_is_true_and_blank_is_false():
    idx = tp.load_league_index()
    assert lp.is_pending(None, "地方业余联赛", idx) is True
    assert idx.reason("地方业余联赛") == "not_found"
    assert lp.is_pending(None, None, idx) is False
    assert lp.is_pending(None, "   ", idx) is False
    assert lp.is_pending(None, "", idx) is False


def test_explicit_pending_wins_over_league_id():
    idx = tp.NameIndex({"英超"}, {tp.norm("待定杯"), tp.norm("临时杯")}, [], authoritative=True)
    assert lp.is_pending(5, "待定杯", idx) is True
    assert lp.is_pending(None, "待定杯", idx) is True
    assert lp.is_pending(0, "陌生杯", idx) is True  # 编号不是升格
    assert lp.is_pending(None, "陌生杯", idx) is True
    assert lp.is_pending(4, "临时杯", idx, "临时杯") is True
    assert lp.is_pending(4, "英超", idx) is False


def test_from_row_uses_common_name_not_empty_id():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (competition_name TEXT, league_id INTEGER, league_canonical TEXT)")
    conn.execute("INSERT INTO t VALUES (?, ?, ?)", ("英格兰超级联赛", None, None))
    conn.execute("INSERT INTO t VALUES (?, ?, ?)", ("地方业余联赛", None, None))
    conn.execute("INSERT INTO t VALUES (?, ?, ?)", ("地方业余联赛", 3, "西甲"))
    conn.execute("INSERT INTO t VALUES (?, ?, ?)", (None, None, None))
    rows = conn.execute("SELECT * FROM t").fetchall()
    flags = [lp.from_row(r) for r in rows]
    assert flags == [False, True, False, False]
    conn.execute("CREATE TABLE bare (competition_name TEXT)")
    conn.execute("INSERT INTO bare VALUES (?)", ("英超",))
    bare = conn.execute("SELECT * FROM bare").fetchone()
    assert lp.from_row(bare) is False


def _match_row(conn, *, name, league_id, home, away, uid="u1"):
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
        ("周六001", 1, "六", "2026-10-18", "jingcai", name, "联赛", None,
         20, "2026-10-18T20:00:00+08:00", 1, home, away, None, None, league_id),
    )
    return conn.execute("SELECT * FROM matches").fetchone()


def test_match_object_stars_only_unestablished_names(tmp_path, monkeypatch):
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(tmp_path / "missing-final.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "missing-seed.csv"))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    pending = tp.load_pending(conn)
    premier = build_match_obj(
        _match_row(conn, name="英格兰超级联赛", league_id=None, home="切尔西", away="杜连斯"),
        {},
        pending,
    )
    assert premier["teams_pending_confirm"] == {"home": False, "away": True}
    assert premier["league_pending_confirm"] is False
    assert premier["competition"]["league_pending_confirm"] is False
    assert premier["competition"]["name"] == "英格兰超级联赛"

    unknown = build_match_obj(
        _match_row(conn, name="地方业余联赛", league_id=None, home="切尔西", away="曼联"),
        {},
        pending,
    )
    assert unknown["league_pending_confirm"] is True
    assert unknown["teams_pending_confirm"] == {"home": False, "away": False}

    linked = build_match_obj(
        _match_row(conn, name="地方业余联赛", league_id=4, home="利兹", away="佛罗伦萨"),
        {},
        pending,
    )
    assert linked["league_pending_confirm"] is True  # 只有编号，来源名尚未升格
    assert linked["teams_pending_confirm"] == {"home": False, "away": False}

    blank = build_match_obj(
        _match_row(conn, name=None, league_id=None, home="切尔西", away="杜连斯"),
        {},
        pending,
    )
    assert blank["league_pending_confirm"] is False


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
        CREATE TABLE predictions (match_id INTEGER, strategy TEXT, direction TEXT);
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
        ("2099-01-02|premier", "英格兰超级联赛", None, "切尔西", "曼联"),
        ("2099-01-02|unknown", "地方业余联赛", None, "杜连斯", "佛罗伦萨"),
        ("2099-01-02|linked", "地方业余联赛", 1, "利兹", "勒沃库森"),
        ("2099-01-02|blank", None, None, "切尔西", "杜连斯"),
    ]
    for uid, name, league_id, home, away in rows:
        conn.execute(
            """INSERT INTO matches (
                 match_uid, scope, jingcai_date, weekday, kickoff_hour, jc_id, jc_no,
                 competition_name, competition_type, home_team, away_team,
                 kickoff_at, kickoff_minute_known, league_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, "jingcai", "2099-01-02", "五", 20, None, None,
             name, "联赛", home, away, "2099-01-02T20:00:00+08:00", 1, league_id),
        )
    conn.commit()
    return conn


def test_table_matches_nested_and_flat(tmp_path, monkeypatch):
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(tmp_path / "missing-final.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "missing-seed.csv"))
    from app import table_matches as tm

    conn = _table_db()
    common = dict(
        date_from="2099-01-02", date_to="2099-01-02", scope="all", strategy="CFFXDJ_5_V3",
        channel="rule", include_live="none", settlement_version=None, as_of=None,
        baseline_window_days=None, baseline_min_n=20, limit=20, offset=0,
    )
    nested = tm.build_table(conn, fmt="nested", **common)
    by_id = {item["match_id"]: item["match"] for item in nested["items"]}
    premier = by_id["2099-01-02|premier"]
    assert premier["league"] == "英格兰超级联赛"
    assert premier["league_pending_confirm"] is False
    assert premier["home_team_pending_confirm"] is False
    assert premier["away_team_pending_confirm"] is False
    unknown = by_id["2099-01-02|unknown"]
    assert unknown["league_pending_confirm"] is True
    assert unknown["home_team_pending_confirm"] is True
    assert unknown["away_team_pending_confirm"] is False
    linked = by_id["2099-01-02|linked"]
    assert linked["league_canonical"] == "西班牙甲级联赛"
    assert linked["league_pending_confirm"] is False
    assert by_id["2099-01-02|blank"]["league_pending_confirm"] is False
    assert by_id["2099-01-02|blank"]["away_team_pending_confirm"] is True
    for match in by_id.values():
        tm.MatchInfo.model_validate(match)

    flat = tm.build_table(conn, fmt="flat", **common)
    flat_by_id = {row["match_id"]: row for row in flat["items"]}
    assert flat_by_id["2099-01-02|premier"]["league_pending_confirm"] is False
    assert flat_by_id["2099-01-02|unknown"]["league_pending_confirm"] is True
    assert flat_by_id["2099-01-02|blank"]["league_pending_confirm"] is False
    keysets = {tuple(sorted(row.keys())) for row in flat["items"]}
    assert len(keysets) == 1
    conn.close()
