"""GET /matches 的规范名与待确认，和 GET /table/matches 同一套名册。

有编号且库里有 name_zh_canonical 时显示规范名；缺编号、缺行或规范名为空白时回退文本列。
星号把回退前的规范名和来源文本一起交给 NameIndex，编号本身不加星也不消星。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.db as adb  # noqa: E402
from app import league_pending as lp  # noqa: E402
from app import team_pending as tp  # noqa: E402
from app.main import CanonicalNames, app, build_match_obj, load_canonical_names  # noqa: E402

DAY = "2099-10-18"

_SUPPORT = """
CREATE TABLE predictions (match_id INTEGER, strategy TEXT, direction TEXT);
CREATE TABLE strategy_defs (strategy_key TEXT, status TEXT, config_json TEXT);
CREATE TABLE results (
  match_id INTEGER PRIMARY KEY,
  home_goals INTEGER, away_goals INTEGER, total_goals INTEGER, wdl TEXT
);
CREATE TABLE match_meta (
  match_id INTEGER PRIMARY KEY, extras_json TEXT, source_file TEXT, month TEXT
);
CREATE TABLE stats (match_id INTEGER PRIMARY KEY);
CREATE TABLE odds_raw (match_id INTEGER PRIMARY KEY, odds_json TEXT);
CREATE TABLE odds_asian (
  match_id INTEGER, book TEXT, phase TEXT, handicap REAL, home_water REAL, away_water REAL
);
CREATE TABLE odds_euro_home (match_id INTEGER, book TEXT, phase TEXT, home_win REAL);
CREATE TABLE odds_jc_home (match_id INTEGER, phase TEXT, home_win REAL);
"""

_MATCHES = """
CREATE TABLE matches (
  id INTEGER PRIMARY KEY,
  match_uid TEXT NOT NULL UNIQUE,
  scope TEXT NOT NULL DEFAULT 'jingcai',
  jingcai_date TEXT NOT NULL,
  weekday TEXT,
  kickoff_hour INTEGER,
  jc_id TEXT,
  jc_no INTEGER,
  competition_name TEXT,
  competition_type TEXT,
  competition_stage TEXT,
  home_team TEXT,
  away_team TEXT,
  kickoff_at TEXT,
  kickoff_minute_known INTEGER NOT NULL DEFAULT 0,
  home_team_id INTEGER,
  away_team_id INTEGER,
  league_id INTEGER
);
"""


@pytest.fixture(autouse=True)
def isolate_name_files(tmp_path, monkeypatch):
    """外部研究目录不参与本文件，避免把包内名册换成另一份定稿。"""
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(tmp_path / "missing-final.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "missing-seed.csv"))
    monkeypatch.setenv(tp.ENV_LEAGUE_FINAL, str(tmp_path / "missing-leagues.csv"))


def _point_db(monkeypatch, path: Path) -> TestClient:
    monkeypatch.setattr(adb, "DB_PATH", path)
    monkeypatch.setattr(adb, "READONLY", False)
    monkeypatch.setattr(adb, "DB_LABEL", "live")
    return TestClient(app)


def _insert_match(conn, **kw):
    conn.execute(
        """INSERT INTO matches (
             id, match_uid, scope, jingcai_date, weekday, kickoff_hour, jc_id, jc_no,
             competition_name, competition_type, competition_stage,
             home_team, away_team, kickoff_at, kickoff_minute_known,
             home_team_id, away_team_id, league_id
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            kw["id"], kw["uid"], "jingcai", DAY, "日", 20, kw.get("jc"), kw.get("no"),
            kw.get("competition"), "联赛", None,
            kw.get("home"), kw.get("away"), f"{DAY}T20:00:00+08:00", 1,
            kw.get("home_id"), kw.get("away_id"), kw.get("league_id"),
        ),
    )


def _build_named_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_MATCHES + _SUPPORT + """
        CREATE TABLE teams (
          id INTEGER PRIMARY KEY, name_zh_canonical TEXT, pending_confirm TEXT
        );
        CREATE TABLE team_aliases (
          id INTEGER PRIMARY KEY, alias TEXT, team_id INTEGER, source TEXT
        );
        CREATE TABLE leagues (
          id INTEGER PRIMARY KEY, name_zh_canonical TEXT, pending_confirm TEXT
        );
        CREATE TABLE league_aliases (
          id INTEGER PRIMARY KEY, alias TEXT, league_id INTEGER, source TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO teams (id, name_zh_canonical, pending_confirm) VALUES (?,?,?)",
        [
            (10, "利兹联", "false"),
            (11, "兹沃勒", "false"),
            (12, "库普斯", "false"),
            (13, "阿拉维斯", "false"),
            (14, "切尔西", "false"),
            (15, None, None),
            (16, "拜仁慕尼黑", "false"),
            (17, "", None),
            (18, "   ", None),
        ],
    )
    conn.executemany(
        "INSERT INTO leagues (id, name_zh_canonical, pending_confirm) VALUES (?,?,?)",
        [
            (1, "英超", "false"),
            (2, "西班牙甲级联赛", "false"),
            (3, "澳超", "false"),
            (4, "临时洲际杯", "true"),
            (7, "瑞典超", "false"),
        ],
    )
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('某待定译名', 16, 'import_pending_confirm')"
    )
    conn.execute(
        "INSERT INTO league_aliases (alias, league_id, source) VALUES ('澳大利亚超级联赛', 3, 'manual')"
    )
    rows = [
        # 日010：主队文本「利兹」→ 规范名「利兹联」；客队无编号，回退文本并保持待确认。
        dict(id=10, uid=f"{DAY}|日010", jc="日010", no=10, competition="英格兰超级联赛", league_id=1,
             home="利兹", home_id=10, away="杜连斯", away_id=None),
        dict(id=4, uid=f"{DAY}|日004", jc="日004", no=4, competition="荷甲", league_id=None,
             home="PEC兹沃勒", home_id=11, away="切尔西", away_id=14),
        dict(id=8, uid=f"{DAY}|日008", jc="日008", no=8, competition="芬超", league_id=None,
             home="古比斯", home_id=12, away="曼联", away_id=None),
        dict(id=15, uid=f"{DAY}|日015", jc="日015", no=15, competition="西甲", league_id=None,
             home="阿拉维斯旧译", home_id=13, away="切尔西女", away_id=14),
        dict(id=7, uid=f"{DAY}|日007", jc="日007", no=7, competition="瑞典超级联赛", league_id=7,
             home="切尔西", home_id=14, away="曼联", away_id=None),
        dict(id=21, uid=f"{DAY}|sweden-no-id", jc="日021", no=21, competition="瑞典超", league_id=None,
             home="切尔西", home_id=None, away="曼联", away_id=None),
        dict(id=22, uid=f"{DAY}|amateur", jc="日022", no=22, competition="地方业余联赛", league_id=None,
             home="切尔西", home_id=None, away="曼联", away_id=None),
        dict(id=23, uid=f"{DAY}|amateur-linked", jc="日023", no=23, competition="地方业余联赛", league_id=2,
             home="切尔西", home_id=None, away="曼联", away_id=None),
        dict(id=24, uid=f"{DAY}|blank-canonical", jc="日024", no=24, competition="英超", league_id=None,
             home="利兹", home_id=15, away="曼联", away_id=None),
        dict(id=25, uid=f"{DAY}|empty-canonical", jc="日025", no=25, competition="英超", league_id=None,
             home="曼联", home_id=17, away="切尔西", away_id=None),
        dict(id=26, uid=f"{DAY}|whitespace-canonical", jc="日026", no=26, competition="英超", league_id=None,
             home="佛罗伦萨", home_id=18, away="切尔西", away_id=None),
        dict(id=27, uid=f"{DAY}|missing-team", jc="日027", no=27, competition="英超", league_id=None,
             home="曼联", home_id=999, away="切尔西", away_id=None),
        dict(id=28, uid=f"{DAY}|pending-alias", jc="日028", no=28, competition="德甲", league_id=None,
             home="某待定译名", home_id=16, away="切尔西", away_id=None),
        dict(id=29, uid=f"{DAY}|aus", jc="日029", no=29, competition="还没有常用名的杯", league_id=3,
             home="切尔西", home_id=None, away="曼联", away_id=None),
        dict(id=30, uid=f"{DAY}|temp-cup", jc="日030", no=30, competition="临时洲际杯", league_id=4,
             home="切尔西", home_id=None, away="曼联", away_id=None),
    ]
    for row in rows:
        _insert_match(conn, **row)
    conn.commit()
    conn.close()


def _matches(client: TestClient) -> dict[str, dict]:
    response = client.get("/matches", params={"date": DAY, "scope": "jingcai"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["date"] == DAY
    return {item["id"]: item["match"] for item in body["items"]}


def _same_pending(match: dict, row: sqlite3.Row, pending, names: CanonicalNames) -> None:
    """与数据表同一调用：规范名用库内原文，没有则传空，再传来源文本。"""
    home_raw = names.team(row["home_team_id"])
    away_raw = names.team(row["away_team_id"])
    league_raw = names.league(row["league_id"])
    assert match["teams_pending_confirm"]["home"] == tp.is_pending(
        row["home_team_id"], home_raw, pending, row["home_team"],
    )
    assert match["teams_pending_confirm"]["away"] == tp.is_pending(
        row["away_team_id"], away_raw, pending, row["away_team"],
    )
    assert match["league_pending_confirm"] == lp.is_pending(
        row["league_id"], row["competition_name"], pending.league, league_raw,
    )
    assert match["home_team_pending_confirm"] == match["teams_pending_confirm"]["home"]
    assert match["away_team_pending_confirm"] == match["teams_pending_confirm"]["away"]
    assert match["competition"]["league_pending_confirm"] == match["league_pending_confirm"]
    assert match["competition"]["canonical"] == match["league_canonical"]
    assert match["teams"]["home_canonical"] == match["home_team_canonical"]
    assert match["teams"]["away_canonical"] == match["away_team_canonical"]
    assert match["competition"]["name"] == row["competition_name"]
    assert match["teams"]["home"] == row["home_team"]
    assert match["teams"]["away"] == row["away_team"]
    assert match["home_team_canonical"] == (home_raw if home_raw is not None else row["home_team"])
    assert match["away_team_canonical"] == (away_raw if away_raw is not None else row["away_team"])
    assert match["league_canonical"] == (league_raw if league_raw is not None else row["competition_name"])


def test_list_and_detail_use_canonical_names(tmp_path, monkeypatch):
    path = tmp_path / "named.db"
    _build_named_db(path)
    client = _point_db(monkeypatch, path)
    by_id = _matches(client)
    assert len(by_id) == 15

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    pending = tp.load_pending(conn)
    names = load_canonical_names(conn)
    stored = conn.execute("SELECT * FROM matches").fetchall()
    conn.close()
    for row in stored:
        _same_pending(by_id[row["match_uid"]], row, pending, names)

    day010 = by_id[f"{DAY}|日010"]
    assert day010["teams"]["home"] == "利兹"
    assert day010["teams"]["home_canonical"] == "利兹联"
    assert day010["home_team_canonical"] == "利兹联"
    assert day010["teams_pending_confirm"]["home"] is False
    assert day010["teams"]["away_canonical"] == "杜连斯"
    assert day010["teams_pending_confirm"]["away"] is True
    assert day010["competition"]["name"] == "英格兰超级联赛"
    assert day010["league_canonical"] == "英超"
    assert day010["league_pending_confirm"] is False

    assert by_id[f"{DAY}|日004"]["home_team_canonical"] == "兹沃勒"
    assert by_id[f"{DAY}|日004"]["teams"]["home"] == "PEC兹沃勒"
    assert by_id[f"{DAY}|日004"]["league_id"] is None
    assert by_id[f"{DAY}|日004"]["league_canonical"] == "荷甲"
    assert by_id[f"{DAY}|日004"]["league_pending_confirm"] is False

    assert by_id[f"{DAY}|日008"]["home_team_canonical"] == "库普斯"
    assert by_id[f"{DAY}|日008"]["teams"]["home"] == "古比斯"
    assert by_id[f"{DAY}|日015"]["home_team_canonical"] == "阿拉维斯"
    assert by_id[f"{DAY}|日015"]["teams"]["home"] == "阿拉维斯旧译"
    # 女子队不能借用一线队规范名消星；显示仍是所链接球队的规范名。
    assert by_id[f"{DAY}|日015"]["teams"]["away"] == "切尔西女"
    assert by_id[f"{DAY}|日015"]["away_team_canonical"] == "切尔西"
    assert by_id[f"{DAY}|日015"]["teams_pending_confirm"]["away"] is True

    day007 = by_id[f"{DAY}|日007"]
    assert day007["competition"]["name"] == "瑞典超级联赛"
    assert day007["competition"]["canonical"] == "瑞典超"
    assert day007["league_canonical"] == "瑞典超"
    assert day007["league_pending_confirm"] is False

    sweden = by_id[f"{DAY}|sweden-no-id"]
    assert sweden["league_id"] is None
    assert sweden["league_canonical"] == "瑞典超"
    assert sweden["league_pending_confirm"] is False

    amateur = by_id[f"{DAY}|amateur"]
    assert amateur["league_canonical"] == "地方业余联赛"
    assert amateur["league_pending_confirm"] is True
    linked = by_id[f"{DAY}|amateur-linked"]
    assert linked["competition"]["name"] == "地方业余联赛"
    assert linked["league_canonical"] == "西班牙甲级联赛"
    assert linked["league_pending_confirm"] is False

    assert by_id[f"{DAY}|blank-canonical"]["home_team_canonical"] == "利兹"
    assert by_id[f"{DAY}|empty-canonical"]["home_team_canonical"] == "曼联"
    assert by_id[f"{DAY}|whitespace-canonical"]["home_team_canonical"] == "佛罗伦萨"
    assert by_id[f"{DAY}|missing-team"]["home_team_canonical"] == "曼联"

    alias = by_id[f"{DAY}|pending-alias"]
    assert alias["teams"]["home"] == "某待定译名"
    assert alias["home_team_canonical"] == "拜仁慕尼黑"
    assert alias["teams_pending_confirm"]["home"] is True

    aus = by_id[f"{DAY}|aus"]
    assert aus["competition"]["name"] == "还没有常用名的杯"
    assert aus["league_canonical"] == "澳超"
    assert aus["league_pending_confirm"] is False
    # 来源名不在名册里会标星；库内规范名已升格则与数据表一样不标星。别名行同样并进名册。
    assert lp.is_pending(None, "还没有常用名的杯", pending.league, None) is True
    assert lp.is_pending(None, "澳大利亚超级联赛", pending.league, None) is False
    assert by_id[f"{DAY}|temp-cup"]["league_canonical"] == "临时洲际杯"
    assert by_id[f"{DAY}|temp-cup"]["league_pending_confirm"] is True

    for uid in (f"{DAY}|日010", f"{DAY}|日004", f"{DAY}|日007", f"{DAY}|日008", f"{DAY}|日015"):
        detail = client.get(f"/matches/{uid}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["match"] == by_id[uid]
    by_pk = client.get("/matches/10")
    assert by_pk.status_code == 200
    assert by_pk.json()["match"] == by_id[f"{DAY}|日010"]


def test_missing_name_tables_fall_back_without_error(tmp_path, monkeypatch):
    path = tmp_path / "bare.db"
    conn = sqlite3.connect(path)
    conn.executescript(_MATCHES + _SUPPORT)
    _insert_match(
        conn, id=1, uid=f"{DAY}|bare-known", jc="日001", no=1,
        competition="瑞典超", home="切尔西", away="杜连斯",
        home_id=10, away_id=None, league_id=7,
    )
    _insert_match(
        conn, id=2, uid=f"{DAY}|bare-unknown", jc="日002", no=2,
        competition="地方业余联赛", home="曼联", away="切尔西",
    )
    conn.commit()
    conn.close()
    client = _point_db(monkeypatch, path)
    by_id = _matches(client)
    known = by_id[f"{DAY}|bare-known"]
    assert known["home_team_canonical"] == "切尔西"
    assert known["teams"]["home_canonical"] == "切尔西"
    assert known["league_canonical"] == "瑞典超"
    assert known["league_pending_confirm"] is False
    assert known["teams_pending_confirm"]["away"] is True
    unknown = by_id[f"{DAY}|bare-unknown"]
    assert unknown["league_canonical"] == "地方业余联赛"
    assert unknown["league_pending_confirm"] is True
    detail = client.get(f"/matches/{DAY}|bare-known")
    assert detail.status_code == 200
    assert detail.json()["match"]["home_team_canonical"] == "切尔西"


def test_matches_without_id_columns_fall_back(tmp_path, monkeypatch):
    path = tmp_path / "no-id.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE matches (
          id INTEGER PRIMARY KEY,
          match_uid TEXT NOT NULL UNIQUE,
          scope TEXT NOT NULL,
          jingcai_date TEXT NOT NULL,
          weekday TEXT,
          kickoff_hour INTEGER,
          jc_id TEXT,
          jc_no INTEGER,
          competition_name TEXT,
          competition_type TEXT,
          competition_stage TEXT,
          home_team TEXT,
          away_team TEXT,
          kickoff_at TEXT,
          kickoff_minute_known INTEGER
        );
        """ + _SUPPORT
    )
    conn.execute(
        """INSERT INTO matches (
             match_uid, scope, jingcai_date, weekday, kickoff_hour, jc_id, jc_no,
             competition_name, competition_type, home_team, away_team, kickoff_at,
             kickoff_minute_known
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (f"{DAY}|noid", "jingcai", DAY, "日", 20, "日001", 1,
         "瑞典超", "联赛", "利兹", "杜连斯", f"{DAY}T20:00:00+08:00"),
    )
    conn.commit()
    conn.close()
    client = _point_db(monkeypatch, path)
    match = _matches(client)[f"{DAY}|noid"]
    assert match["home_team_id"] is None
    assert match["league_id"] is None
    assert match["home_team_canonical"] == "利兹"
    assert match["teams"]["home"] == "利兹"
    assert match["away_team_canonical"] == "杜连斯"
    assert match["league_canonical"] == "瑞典超"
    assert match["league_pending_confirm"] is False
    assert match["teams_pending_confirm"] == {"home": False, "away": True}


def _row(conn, **cols):
    names = list(cols)
    conn.execute(f"CREATE TABLE matches ({', '.join(f'{name} TEXT' for name in names)})")
    conn.execute(
        f"INSERT INTO matches ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
        [cols[name] for name in names],
    )
    return conn.execute("SELECT * FROM matches").fetchone()


def test_row_canonical_column_matches_table_pending():
    """查询已经选出规范名列时，不必再传编号字典。待确认仍看原文，不看回退后的显示名。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = _row(
        conn,
        jc_id=None, jc_no=None, weekday="日", jingcai_date=DAY, scope="jingcai",
        competition_name="地方业余联赛", competition_type="联赛", competition_stage=None,
        kickoff_hour=20, kickoff_at=None, kickoff_minute_known=0,
        home_team="利兹", away_team="切尔西女",
        league_canonical="西班牙甲级联赛", home_team_canonical="利兹联", away_canonical="切尔西",
    )
    pending = tp.load_pending(None)
    match = build_match_obj(row, {}, pending)
    assert match["competition"]["name"] == "地方业余联赛"
    assert match["league_canonical"] == "西班牙甲级联赛"
    assert match["league_pending_confirm"] is False
    assert match["home_team_canonical"] == "利兹联"
    assert match["teams"]["home"] == "利兹"
    assert match["away_team_canonical"] == "切尔西"
    assert match["teams"]["away"] == "切尔西女"
    assert match["teams_pending_confirm"]["away"] is True
    assert match["league_pending_confirm"] == lp.is_pending(
        None, "地方业余联赛", pending.league, "西班牙甲级联赛",
    )


def test_lookup_prefers_id_and_established_canonical_clears_star():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = _row(
        conn,
        jc_id=None, jc_no=None, weekday="日", jingcai_date=DAY, scope="jingcai",
        competition_name="地方业余联赛", competition_type="联赛", competition_stage=None,
        kickoff_hour=20, kickoff_at=None, kickoff_minute_known=0,
        home_team="陌生旧译", away_team="切尔西",
        home_team_id="1", away_team_id=None, league_id="7",
        league_canonical="行上的旧名",
    )
    teams = tp.NameIndex({tp.norm("切尔西")}, set(), [], authoritative=True)
    teams.league = tp.NameIndex({tp.norm("瑞典超")}, set(), [], authoritative=True)
    names = CanonicalNames(teams={1: "切尔西"}, leagues={7: "瑞典超"})
    match = build_match_obj(row, {}, teams, canonicals=names)
    assert match["home_team_canonical"] == "切尔西"
    assert match["teams"]["home"] == "陌生旧译"
    assert match["teams_pending_confirm"]["home"] is False
    assert match["league_canonical"] == "瑞典超"
    assert match["league_pending_confirm"] is False
    assert tp.is_pending(None, None, teams, "陌生旧译") is True

    unlinked = _row_unlinked(row)
    bare = build_match_obj(unlinked, {}, teams)
    assert bare["home_team_canonical"] == "陌生旧译"
    assert bare["teams_pending_confirm"]["home"] is True


def _row_unlinked(source: sqlite3.Row) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return _row(
        conn,
        jc_id=None, jc_no=None, weekday="日", jingcai_date=DAY, scope="jingcai",
        competition_name=source["competition_name"], competition_type="联赛", competition_stage=None,
        kickoff_hour=20, kickoff_at=None, kickoff_minute_known=0,
        home_team=source["home_team"], away_team=source["away_team"],
    )
