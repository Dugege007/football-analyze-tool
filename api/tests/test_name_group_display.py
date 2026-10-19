"""名称组没有常用名时，用组内默认显示，并保持待确认。

已有常用名时显示常用名且不标星。来源名、常用名、别名都空时仍标星。
编号本身不能消星。外部别名只参与显示，不参与消星。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import team_pending as tp  # noqa: E402
from app.main import CanonicalNames, build_match_obj, load_canonical_names  # noqa: E402

DAY = "2099-10-19"


def test_aliases_for_tip_drops_shown_name_and_duplicates():
    aliases = ["红星FC 93", "  红星  ", "圣旺红星", "Red Star", "红星", "红星FC 93", "  ", None]
    assert tp.aliases_for_tip(aliases, "红星", "红星") == ["红星FC 93", "圣旺红星", "Red Star"]
    assert tp.aliases_for_tip(aliases, None, "红星FC 93") == ["红星", "圣旺红星", "Red Star"]
    assert tp.aliases_for_tip(["布里斯班狮吼", "Brisbane Roar"], "布里斯班狮吼") == ["Brisbane Roar"]
    assert tp.aliases_for_tip([], "红星") == []
    assert tp.aliases_for_tip(None, None) == []


def test_group_default_prefers_canonical_then_source_then_entity_then_alias():
    aliases = ["Brisbane Roar", "布里斯班狮吼", "狮吼"]
    assert tp.group_default_display("切尔西", "Chelsea", aliases, entity_name="实体暂名") == "切尔西"
    assert tp.group_default_display("  ", "来源名", aliases, entity_name="实体暂名") == "来源名"
    assert tp.group_default_display(None, "   ", aliases, entity_name="实体暂名") == "实体暂名"
    assert tp.group_default_display(None, None, aliases) == "布里斯班狮吼"
    assert tp.group_default_display(None, None, ["Brisbane Roar"]) == "Brisbane Roar"
    assert tp.group_default_display(None, None, ["", "  ", None]) is None
    assert tp.table_display_canonical(None, "来源名", aliases) is None
    assert tp.table_display_canonical("常用名", None, aliases) == "常用名"
    assert tp.table_display_canonical(None, None, aliases, entity_name="实体暂名") == "实体暂名"
    assert tp.table_display_canonical("   ", "   ", ["只有别名"]) == "只有别名"


def test_match_labels_follow_name_group(tmp_path, monkeypatch):
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(tmp_path / "missing-final.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "missing-seed.csv"))
    monkeypatch.setenv(tp.ENV_LEAGUE_FINAL, str(tmp_path / "missing-leagues.csv"))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE matches (
          jc_id TEXT, jc_no INTEGER, weekday TEXT, jingcai_date TEXT, scope TEXT,
          competition_name TEXT, competition_type TEXT, competition_stage TEXT,
          kickoff_hour INTEGER, kickoff_at TEXT, kickoff_minute_known INTEGER,
          home_team TEXT, away_team TEXT,
          home_team_id INTEGER, away_team_id INTEGER, league_id INTEGER
        );
        CREATE TABLE teams (id INTEGER PRIMARY KEY, name_zh_canonical TEXT, pending_confirm TEXT);
        CREATE TABLE leagues (id INTEGER PRIMARY KEY, name_zh_canonical TEXT, pending_confirm TEXT);
        CREATE TABLE team_aliases (id INTEGER PRIMARY KEY, alias TEXT, team_id INTEGER, source TEXT);
        CREATE TABLE league_aliases (id INTEGER PRIMARY KEY, alias TEXT, league_id INTEGER, source TEXT);
        """
    )
    conn.execute("INSERT INTO teams (id, name_zh_canonical, pending_confirm) VALUES (1, '切尔西', 'false')")
    conn.execute("INSERT INTO teams (id, name_zh_canonical, pending_confirm) VALUES (2, NULL, NULL)")
    conn.execute("INSERT INTO teams (id, name_zh_canonical, pending_confirm) VALUES (3, '   ', NULL)")
    conn.execute("INSERT INTO leagues (id, name_zh_canonical, pending_confirm) VALUES (7, '英超', 'false')")
    conn.execute("INSERT INTO leagues (id, name_zh_canonical, pending_confirm) VALUES (8, NULL, NULL)")
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('切尔西英文', 1, 'manual')"
    )
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('Brisbane Roar', 2, 'bsd')"
    )
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('布里斯班狮吼', 2, 'bsd')"
    )
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('某实体别名', 3, 'bsd')"
    )
    conn.execute(
        "INSERT INTO league_aliases (alias, league_id, source) VALUES ('A-League Cup', 8, 'bsd')"
    )
    conn.execute(
        "INSERT INTO league_aliases (alias, league_id, source) VALUES ('澳超杯预选', 8, 'bsd')"
    )
    conn.execute(
        """INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (None, None, "日", DAY, "jingcai", None, "联赛", None,
         20, None, 0, None, None, 1, 2, 7),
    )
    promoted = conn.execute("SELECT * FROM matches").fetchone()
    conn.execute("DELETE FROM matches")
    conn.execute(
        """INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (None, None, "日", DAY, "jingcai", None, "联赛", None,
         20, None, 0, "尚未升格的来源名", None, None, 3, 8),
    )
    grouped = conn.execute("SELECT * FROM matches").fetchone()
    conn.execute("DELETE FROM matches")
    conn.execute(
        """INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (None, None, "日", DAY, "jingcai", "   ", "联赛", None,
         20, None, 0, None, "   ", 99, 2, 88),
    )
    empty = conn.execute("SELECT * FROM matches").fetchone()
    conn.commit()
    names = load_canonical_names(conn)
    pending = tp.NameIndex({tp.norm("切尔西")}, set(), [], authoritative=True)
    pending.league = tp.NameIndex({tp.norm("英超")}, set(), [], authoritative=True)

    shown = build_match_obj(promoted, {}, pending, canonicals=names)
    assert shown["home_team_canonical"] == "切尔西"
    assert shown["teams"]["home_canonical"] == "切尔西"
    assert shown["home_team_aliases"] == ["切尔西英文"]
    assert "切尔西" not in shown["home_team_aliases"]
    assert shown["home_team_pending_confirm"] is False
    assert shown["teams_pending_confirm"]["home"] is False
    assert shown["league_canonical"] == "英超"
    assert shown["league_pending_confirm"] is False
    assert shown["away_team_canonical"] == "布里斯班狮吼"
    assert shown["away_team_aliases"] == ["Brisbane Roar"]
    assert "布里斯班狮吼" not in shown["away_team_aliases"]
    assert shown["away_team_pending_confirm"] is True
    assert tp.reason(2, None, pending, None) == "name_absent"

    fallback = build_match_obj(grouped, {}, pending, canonicals=names)
    assert fallback["home_team_canonical"] == "尚未升格的来源名"
    assert fallback["home_team_aliases"] == []
    assert fallback["home_team_pending_confirm"] is True
    assert fallback["away_team_canonical"] == "某实体别名"
    assert fallback["away_team_aliases"] == []
    assert fallback["away_team_pending_confirm"] is True
    assert fallback["league_canonical"] == "澳超杯预选"
    assert fallback["competition"]["canonical"] == "澳超杯预选"
    assert fallback["league_pending_confirm"] is True
    assert fallback["competition"]["name"] is None
    # 别名文本即使写进显示名，也不进入消星判断。编号同样不能消星。
    assert tp.is_pending(8, None, pending.league, None) is True
    assert tp.reason(8, None, pending.league, None) == "name_absent"

    blank = build_match_obj(empty, {}, pending, canonicals=names)
    assert blank["league_canonical"] is None
    assert blank["home_team_canonical"] is None
    assert blank["home_team_aliases"] == []
    assert blank["away_team_canonical"] == "布里斯班狮吼"
    assert blank["away_team_aliases"] == ["Brisbane Roar"]
    assert blank["league_pending_confirm"] is True
    assert blank["home_team_pending_confirm"] is True
    assert blank["away_team_pending_confirm"] is True
    assert tp.is_pending(99, None, pending, None) is True
    assert tp.is_pending(None, None, pending, "切尔西") is False
    conn.close()


def test_table_uses_group_default_without_clearing_star(tmp_path, monkeypatch):
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(tmp_path / "missing-final.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "missing-seed.csv"))
    monkeypatch.setenv(tp.ENV_LEAGUE_FINAL, str(tmp_path / "missing-leagues.csv"))
    from app import table_matches as tm

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
        CREATE TABLE league_aliases (id INTEGER PRIMARY KEY, alias TEXT, league_id INTEGER, source TEXT);
        CREATE TABLE team_aliases (id INTEGER PRIMARY KEY, alias TEXT, team_id INTEGER, source TEXT);
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
    conn.execute("INSERT INTO leagues (id, name_zh_canonical) VALUES (7, '英超')")
    conn.execute("INSERT INTO leagues (id, name_zh_canonical) VALUES (8, NULL)")
    conn.execute("INSERT INTO teams (id, name_zh_canonical) VALUES (1, '切尔西')")
    conn.execute("INSERT INTO teams (id, name_zh_canonical) VALUES (2, NULL)")
    conn.execute("INSERT INTO teams (id, name_zh_canonical) VALUES (3, NULL)")
    conn.execute("INSERT INTO teams (id, name_zh_canonical) VALUES (304, '红星')")
    conn.execute("INSERT INTO league_aliases (alias, league_id, source) VALUES ('澳超杯预选', 8, 'bsd')")
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('切尔西英文', 1, 'manual')")
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('布里斯班狮吼', 2, 'bsd')")
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('Brisbane Roar', 2, 'bsd')")
    for alias in ("红星FC 93", "圣旺红星", "Red Star", "Red Star FC 93", "Red Star FC", "红星"):
        conn.execute(
            "INSERT INTO team_aliases (alias, team_id, source) VALUES (?, 304, 'preferred_name_map|legacy_high_confidence')",
            (alias,),
        )
    rows = [
        ("2099-01-02|promoted", "地方业余联赛", 7, "旧译切尔西", 1, "曼联", None),
        ("2099-01-02|grouped", None, 8, "杜连斯", None, None, 2),
        ("2099-01-02|empty", None, 88, None, 99, "   ", 3),
        ("2099-01-02|source-only", "地方业余联赛", None, "杜连斯", None, "切尔西", None),
        ("2099-01-02|redstar", "法乙", None, "红星", 304, "格勒诺布尔", None),
    ]
    for uid, league, league_id, home, home_id, away, away_id in rows:
        conn.execute(
            """INSERT INTO matches (
                 match_uid, scope, jingcai_date, weekday, kickoff_hour, jc_id, jc_no,
                 competition_name, competition_type, home_team, away_team,
                 kickoff_at, kickoff_minute_known, league_id, home_team_id, away_team_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, "jingcai", "2099-01-02", "五", 20, None, None,
             league, "联赛", home, away, "2099-01-02T20:00:00+08:00", 1,
             league_id, home_id, away_id),
        )
    conn.commit()
    nested = tm.build_table(
        conn, fmt="nested", date_from="2099-01-02", date_to="2099-01-02", scope="all",
        strategy="CFFXDJ_5_V3", channel="rule", include_live="none", settlement_version=None,
        as_of=None, baseline_window_days=None, baseline_min_n=20, limit=20, offset=0,
    )
    by_id = {item["match_id"]: item["match"] for item in nested["items"]}
    promoted = by_id["2099-01-02|promoted"]
    assert promoted["league_canonical"] == "英超"
    assert promoted["league_pending_confirm"] is False
    assert promoted["home_team_canonical"] == "切尔西"
    assert promoted["home_team_aliases"] == ["切尔西英文"]
    assert promoted["home_team_pending_confirm"] is False
    assert promoted["away_team_aliases"] == []

    grouped = by_id["2099-01-02|grouped"]
    assert grouped["league"] is None
    assert grouped["league_canonical"] == "澳超杯预选"
    assert grouped["league_pending_confirm"] is True
    assert grouped["home_team"] == "杜连斯"
    assert grouped["home_team_canonical"] is None
    assert grouped["home_team_pending_confirm"] is True
    assert grouped["away_team_canonical"] == "布里斯班狮吼"
    assert grouped["away_team_aliases"] == ["Brisbane Roar"]
    assert grouped["home_team_aliases"] == []
    assert grouped["away_team_pending_confirm"] is True

    empty = by_id["2099-01-02|empty"]
    assert empty["league_canonical"] is None
    assert empty["home_team_canonical"] is None
    assert empty["away_team_canonical"] is None
    assert empty["league_pending_confirm"] is True
    assert empty["home_team_pending_confirm"] is True
    assert empty["away_team_pending_confirm"] is True

    source_only = by_id["2099-01-02|source-only"]
    assert source_only["home_team"] == "杜连斯"
    assert source_only["home_team_aliases"] == []
    assert source_only["away_team_aliases"] == []
    assert source_only["home_team_pending_confirm"] is True
    assert source_only["league_canonical"] is None
    assert source_only["league"] == "地方业余联赛"
    assert source_only["league_pending_confirm"] is True
    assert source_only["away_team"] == "切尔西"
    assert source_only["away_team_canonical"] is None
    assert source_only["away_team_pending_confirm"] is False

    redstar = by_id["2099-01-02|redstar"]
    assert redstar["home_team"] == "红星"
    assert redstar["home_team_canonical"] == "红星"
    assert redstar["home_team_aliases"] == ["红星FC 93", "圣旺红星", "Red Star", "Red Star FC 93", "Red Star FC"]
    assert "红星" not in redstar["home_team_aliases"]
    assert redstar["away_team"] == "格勒诺布尔"
    assert redstar["away_team_aliases"] == []
    # 别名列表不参与消星，待确认仍只看常用名和来源文本。
    assert redstar["home_team_pending_confirm"] == tp.is_pending(304, "红星", tp.load_pending(conn), "红星")
    assert redstar["away_team_pending_confirm"] == tp.is_pending(None, None, tp.load_pending(conn), "格勒诺布尔")

    flat = tm.build_table(
        conn, fmt="flat", date_from="2099-01-02", date_to="2099-01-02", scope="all",
        strategy="CFFXDJ_5_V3", channel="rule", include_live="none", settlement_version=None,
        as_of=None, baseline_window_days=None, baseline_min_n=20, limit=20, offset=0,
    )
    flat_by_id = {row["match_id"]: row for row in flat["items"]}
    assert flat_by_id["2099-01-02|grouped"]["league_canonical"] == "澳超杯预选"
    assert flat_by_id["2099-01-02|grouped"]["league_pending_confirm"] is True
    assert flat_by_id["2099-01-02|empty"]["home_team_pending_confirm"] is True
    assert flat_by_id["2099-01-02|promoted"]["home_team_pending_confirm"] is False
    assert flat_by_id["2099-01-02|redstar"]["home_team_aliases"] == [
        "红星FC 93", "圣旺红星", "Red Star", "Red Star FC 93", "Red Star FC",
    ]
    assert "红星" not in flat_by_id["2099-01-02|redstar"]["home_team_aliases"]
    assert len({tuple(sorted(row.keys())) for row in flat["items"]}) == 1
    tm.MatchInfo.model_validate(redstar)
    conn.close()
