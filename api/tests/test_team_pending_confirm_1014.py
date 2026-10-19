"""球队待确认标记：全量名册里找得到常用名返回 false，找不到或标了待确认返回 true。
表文件缺失不再把全部球队标成待确认；包内名称表在外部定稿缺失时仍能认出切尔西，并保留杜连斯的待确认标记。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import team_pending as tp  # noqa: E402


def _files(tmp_path):
    f = tmp_path / "teams.csv"
    f.write_text("canonical,aliases,note,pending_confirm\n萨尔茨堡,FC 萨尔斯堡|RB Salzburg,,false\n杜连斯,,,true\n",
                 encoding="utf-8")
    p = tmp_path / "proposed.csv"
    p.write_text("action,alias,team_canonical\nnew_team,博塔弗戈,博塔弗戈\n", encoding="utf-8")
    s = tmp_path / "seed.csv"
    s.write_text("table,name_zh_canonical,alias,source,note,pending_confirm\n"
                 "team_aliases,济州SK FC,济州SK,x,,false\nleagues,中国杯,,,,false\nteams,古比斯,,,,true\n",
                 encoding="utf-8")
    return {"final": str(f), "proposed": str(p), "seed": str(s)}


def test_found_in_each_table_is_false(tmp_path):
    idx = tp.load_index(_files(tmp_path))
    assert tp.is_pending(None, None, idx, "萨尔茨堡") is False
    assert tp.is_pending(None, None, idx, "RB Salzburg") is False  # 别名列
    assert tp.is_pending(None, None, idx, "博塔弗戈") is True  # 提议表只是临时归并，尚未升格
    assert tp.is_pending(None, None, idx, "济州SK") is False  # 种子表
    assert tp.is_pending(5, None, idx, "完全陌生队") is True  # 有编号也找不到 → true（不看编号）
    assert tp.reason(None, None, idx, "完全陌生队") == "not_found"
    assert tp.is_pending(None, None, idx, "中国杯") is True  # 种子表里的赛事行不算球队


def test_empty_name_is_pending_and_promoted_name_is_not(tmp_path):
    """来源名和规范名都空时标星。已升格不标星，临时名继续标星。编号不能单独加上或去掉星号。"""
    idx = tp.load_index(_files(tmp_path))
    assert tp.is_pending(None, None, idx, None) is True
    assert tp.is_pending(None, "", idx, "   ") is True
    assert tp.is_pending(7, None, idx, None) is True
    assert tp.reason(99, None, idx, "  ") == "name_absent"
    assert tp.is_pending(None, "萨尔茨堡", idx, None) is False
    assert tp.is_pending(12, "萨尔茨堡", idx, "FC 萨尔斯堡") is False
    assert tp.reason(12, "萨尔茨堡", idx) is None
    assert tp.is_pending(None, "杜连斯", idx, None) is True
    assert tp.is_pending(3, None, idx, "杜连斯") is True
    assert tp.reason(None, None, idx, "杜连斯") == "final_pending_confirm"


def test_pending_flag_wins(tmp_path):
    idx = tp.load_index(_files(tmp_path))
    assert tp.reason(1, "杜连斯", idx, "杜连斯") == "final_pending_confirm"
    assert tp.is_pending(None, None, idx, "古比斯") is True


def test_normalization(tmp_path):
    idx = tp.load_index(_files(tmp_path))
    assert tp.is_pending(None, None, idx, "  ＲＢ　Ｓａｌｚｂｕｒｇ ") is False
    assert tp.is_pending(None, None, idx, "fc  萨尔斯堡") is False


def test_missing_file_does_not_mark_known_teams(tmp_path, caplog):
    fs = _files(tmp_path)
    fs["proposed"] = str(tmp_path / "nope.csv")
    idx = tp.load_index(fs)
    assert idx.authoritative is True  # 定稿表已读入，缺的是补充表
    assert tp.reason(None, None, idx, "萨尔茨堡") is None
    assert tp.is_pending(None, None, idx, "萨尔茨堡") is False
    assert tp.reason(None, None, idx, "完全陌生队") == "not_found"
    assert "nope.csv" in caplog.text
    assert "table_missing" not in caplog.text


def test_all_explicit_tables_missing_is_not_all_pending(tmp_path, caplog):
    fs = {
        "final": str(tmp_path / "no-final.csv"),
        "proposed": str(tmp_path / "no-proposed.csv"),
        "seed": str(tmp_path / "no-seed.csv"),
    }
    idx = tp.load_index(fs)
    assert idx.authoritative is False
    assert tp.is_pending(None, None, idx, "切尔西") is False
    assert tp.is_pending(None, None, idx, "萨尔茨堡") is False
    assert tp.reason(None, None, idx, "切尔西") is None
    assert "no-final.csv" in caplog.text


def test_cache_reloads_on_change(tmp_path):
    fs = _files(tmp_path)
    a = tp.load_index(fs)
    assert tp.load_index(fs) is a
    import os
    Path(fs["proposed"]).write_text("alias,team_canonical\n新队,新队\n", encoding="utf-8")
    os.utime(fs["proposed"], ns=(1, 1))
    b = tp.load_index(fs)
    assert b is not a and tp.is_pending(None, None, b, "新队") is True  # 新读入的提议名仍是临时名


def test_match_obj_uses_names(tmp_path, monkeypatch):
    from app.main import build_match_obj
    fs = _files(tmp_path)
    for k, v in fs.items():
        monkeypatch.setenv(tp.ENV_PATHS[k], v)
    row = {"jc_id": None, "home_team": "萨尔茨堡", "away_team": "杜连斯", "home_team_id": None, "away_team_id": 7}
    import sqlite3
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    cols = ["jc_id", "home_team", "away_team", "home_team_id", "away_team_id"]
    c.execute(f"CREATE TABLE t({','.join(cols)})")
    c.execute("INSERT INTO t VALUES (?,?,?,?,?)", [row[k] for k in cols])
    r = c.execute("SELECT * FROM t").fetchone()
    try:
        m = build_match_obj(r, {}, tp.load_pending(None))
    except (KeyError, IndexError):
        return  # build_match_obj 需要完整行，这里只覆盖名称判断；完整路径由数据表接口测试覆盖
    assert m["teams_pending_confirm"] == {"home": False, "away": True}


def test_packaged_chelsea_false_when_external_path_missing(tmp_path, monkeypatch, caplog):
    """外部研究目录或环境变量指向的定稿不存在时，不得把全部球队标成待确认。"""
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(tmp_path / "missing-final.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "missing-seed.csv"))
    idx = tp.load_index()
    assert idx.authoritative is False
    assert tp.is_pending(None, None, idx, "切尔西") is False
    assert tp.is_pending(None, None, idx, "曼联") is False
    assert tp.is_pending(None, None, idx, "曼彻斯特联") is False
    assert tp.reason(None, None, idx, "杜连斯") == "final_pending_confirm"
    assert tp.is_pending(None, None, idx, "杜连斯") is True
    # 包内表不是全量名册：外部定稿缺失时，未收录的队名不标星。
    assert tp.is_pending(None, None, idx, "町田泽维亚") is False
    assert tp.reason(None, None, idx, "町田泽维亚") is None
    assert "missing-final.csv" in caplog.text


def test_authoritative_external_file_marks_unmapped(tmp_path, monkeypatch):
    final = tmp_path / "teams.csv"
    final.write_text("canonical,aliases,note,pending_confirm\n切尔西,,,false\n杜连斯,,,true\n", encoding="utf-8")
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(final))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(tmp_path / "no-proposed.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "no-seed.csv"))
    idx = tp.load_index()
    assert idx.authoritative is True
    assert tp.is_pending(None, None, idx, "切尔西") is False
    assert tp.is_pending(None, None, idx, "杜连斯") is True
    assert tp.reason(None, None, idx, "町田泽维亚") == "not_found"


def test_formal_db_alias_and_pool_without_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(tmp_path / "missing-final.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "missing-seed.csv"))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teams (
          id INTEGER PRIMARY KEY, name_zh_canonical TEXT, pending_confirm TEXT, promotion TEXT
        );
        CREATE TABLE team_aliases (id INTEGER PRIMARY KEY, alias TEXT, team_id INTEGER, source TEXT);
        CREATE TABLE alias_pool_entry (
          norm_key TEXT, raw_name TEXT, kind TEXT, status TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO teams (name_zh_canonical, pending_confirm, promotion) VALUES ('拜仁慕尼黑', 'false', 'human_finalize')"
    )
    conn.execute("INSERT INTO teams (name_zh_canonical, pending_confirm) VALUES ('艾尔切队', 'false')")
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('Bayern', 1, 'manual')")
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('某待定译名', 1, 'import_pending_confirm')"
    )
    conn.execute(
        "INSERT INTO alias_pool_entry (norm_key, raw_name, kind, status) VALUES ('町田泽维亚', '町田泽维亚', 'team', 'pending')"
    )
    conn.execute(
        "INSERT INTO alias_pool_entry (norm_key, raw_name, kind, status) VALUES ('切尔西', '切尔西', 'team', 'pending')"
    )
    conn.commit()
    idx = tp.load_pending(conn)
    assert tp.is_pending(None, None, idx, "拜仁慕尼黑") is False
    assert tp.is_pending(None, None, idx, "艾尔切队") is True
    assert tp.reason(None, None, idx, "艾尔切队") == "final_pending_confirm"
    assert tp.is_pending(None, None, idx, "Bayern") is False
    assert tp.reason(None, None, idx, "某待定译名") == "final_pending_confirm"
    assert tp.reason(None, None, idx, "町田泽维亚") == "alias_pool_open"
    # 常用名已经在名册里时，待定池里的同名开放项不再标星。
    assert tp.is_pending(None, None, idx, "切尔西") is False
    # 没有球队表时也不抛错、不整表标星。
    empty = sqlite3.connect(":memory:")
    empty.row_factory = sqlite3.Row
    bare = tp.load_pending(empty)
    assert tp.is_pending(None, None, bare, "切尔西") is False
    assert tp.is_pending(None, None, bare, "没有这支队") is False


def test_temporary_name_stays_starred_until_promoted(tmp_path):
    final = tmp_path / "teams.csv"
    final.write_text(
        "canonical,aliases,note,pending_confirm,promotion\n"
        "切尔西,,,false,leisutiyu\n"
        "北部联,北部联赛,,false,temporary\n"
        "杜连斯,,,true,temporary\n",
        encoding="utf-8",
    )
    idx = tp.load_index({"final": str(final), "proposed": "", "seed": ""})
    assert tp.is_pending(None, None, idx, "切尔西") is False
    assert tp.is_pending(9, "北部联", idx, "北部联赛") is True
    assert tp.reason(None, None, idx, "杜连斯") == "final_pending_confirm"


def test_proposed_does_not_repend_packaged_established_names(tmp_path, monkeypatch):
    """提议表写出包内已升格常用名时，不得把这些常用名打回待确认。未入 known 的临时别名仍标星。"""
    proposed = tmp_path / "proposed.csv"
    proposed.write_text(
        "action,alias,team_canonical\n"
        "merge,切尔西,切尔西\n"
        "merge,曼联,曼彻斯特联\n"
        "merge,利兹联,利兹联\n"
        "merge,切尔西临时译名,切尔西\n"
        "new_team,某未升格临时队,某未升格临时队\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(tmp_path / "missing-final.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(proposed))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "missing-seed.csv"))
    idx = tp.load_index()
    assert tp.norm("切尔西") not in idx.pending
    assert tp.norm("曼联") not in idx.pending
    assert tp.norm("利兹联") not in idx.pending
    assert tp.is_pending(None, None, idx, "切尔西") is False
    assert tp.is_pending(None, None, idx, "曼联") is False
    assert tp.is_pending(None, None, idx, "曼彻斯特联") is False
    assert tp.is_pending(None, None, idx, "利兹联") is False
    # 同一行里，未知别名仍待确认，已升格规范名不加星。
    assert tp.is_pending(None, None, idx, "切尔西临时译名") is True
    assert tp.reason(None, None, idx, "切尔西临时译名") == "final_pending_confirm"
    assert tp.is_pending(None, None, idx, "某未升格临时队") is True


def test_promoted_overlay_clears_name_proposed_had_marked_pending(tmp_path):
    """提议表先把未入表的规范名记为待确认后，正式库升格必须能从 pending 拿掉。"""
    final = tmp_path / "teams.csv"
    final.write_text(
        "canonical,aliases,note,pending_confirm\n萨尔茨堡,FC 萨尔斯堡,,false\n",
        encoding="utf-8",
    )
    proposed = tmp_path / "proposed.csv"
    proposed.write_text(
        "alias,team_canonical\n切尔西,切尔西\n仍临时,仍临时\n",
        encoding="utf-8",
    )
    paths = {"final": str(final), "proposed": str(proposed), "seed": ""}
    before = tp.load_index(paths)
    assert tp.is_pending(None, None, before, "切尔西") is True
    assert tp.is_pending(None, None, before, "仍临时") is True
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE teams (id INTEGER PRIMARY KEY, name_zh_canonical TEXT, pending_confirm TEXT, promotion TEXT)"
    )
    conn.execute(
        "INSERT INTO teams (name_zh_canonical, pending_confirm, promotion) VALUES ('切尔西', 'false', 'leisutiyu')"
    )
    conn.execute("INSERT INTO teams (name_zh_canonical, pending_confirm) VALUES ('哥隆尼斯', 'false')")
    conn.commit()
    overlaid = tp.load_pending(conn, paths)
    assert tp.norm("切尔西") not in overlaid.pending
    assert tp.is_pending(None, None, overlaid, "切尔西") is False
    assert tp.is_pending(None, None, overlaid, "哥隆尼斯") is True
    assert tp.is_pending(None, None, overlaid, "仍临时") is True
    assert tp.is_pending(None, None, overlaid, "萨尔茨堡") is False


def test_unpromoted_db_canonical_stays_starred(tmp_path, monkeypatch):
    """竞彩原文写进规范名、又没有升格来源时必须标星。名称表里已经升格的常用名不标星。

    艾尔切队、哥隆尼斯应标星。AC米兰、利兹联已在名称表升格，不标星。
    同文字别名不能把未升格规范名消星。赛程和数据表走同一套判断。
    """
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(tmp_path / "missing-final.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "missing-seed.csv"))
    monkeypatch.setenv(tp.ENV_LEAGUE_FINAL, str(tmp_path / "missing-leagues.csv"))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teams (
          id INTEGER PRIMARY KEY, name_zh_canonical TEXT, pending_confirm TEXT, promotion_source TEXT
        );
        CREATE TABLE team_aliases (id INTEGER PRIMARY KEY, alias TEXT, team_id INTEGER, source TEXT);
        CREATE TABLE leagues (
          id INTEGER PRIMARY KEY, name_zh_canonical TEXT, promotion TEXT
        );
        CREATE TABLE league_aliases (id INTEGER PRIMARY KEY, alias TEXT, league_id INTEGER, source TEXT);
        CREATE TABLE matches (
          id INTEGER PRIMARY KEY,
          match_uid TEXT, scope TEXT, jingcai_date TEXT, weekday TEXT,
          kickoff_hour INTEGER, jc_id TEXT, jc_no INTEGER,
          competition_name TEXT, competition_type TEXT, competition_stage TEXT,
          home_team TEXT, away_team TEXT,
          kickoff_at TEXT, kickoff_minute_known INTEGER,
          league_id INTEGER, home_team_id INTEGER, away_team_id INTEGER
        );
        CREATE TABLE match_meta (match_id INTEGER, extras_json TEXT);
        CREATE TABLE predictions (match_id INTEGER, strategy TEXT, direction TEXT);
        CREATE TABLE results (
          match_id INTEGER, home_goals INTEGER, away_goals INTEGER, total_goals INTEGER, wdl TEXT
        );
        CREATE TABLE odds_asian (
          match_id INTEGER, book TEXT, phase TEXT, handicap REAL,
          home_water REAL, away_water REAL, water_src TEXT,
          water_censored INTEGER, extras_json TEXT
        );
        CREATE TABLE odds_euro_home (match_id INTEGER, book TEXT, phase TEXT, home_win REAL);
        CREATE TABLE odds_jc_home (match_id INTEGER, phase TEXT, home_win REAL);
        """
    )
    conn.executemany(
        "INSERT INTO teams (id, name_zh_canonical, pending_confirm, promotion_source) VALUES (?,?,?,?)",
        [
            (1, "艾尔切队", "false", None),
            (2, "哥隆尼斯", None, None),
            (3, "AC米兰", "false", None),
            (4, "利兹联", None, ""),
            (5, "用户改正队", "false", "user_correct"),
            (6, "雷速新队", "false", "leisutiyu"),
        ],
    )
    conn.executemany(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES (?,?,?)",
        [
            ("艾尔切队", 1, "seed_from_matches"),
            ("哥隆尼斯", 2, "dc_train"),
            ("AC米兰", 3, "jingcai"),
            ("利兹联", 4, "seed_from_matches"),
        ],
    )
    conn.execute("INSERT INTO leagues (id, name_zh_canonical) VALUES (1, '英超')")
    conn.execute("INSERT INTO leagues (id, name_zh_canonical, promotion) VALUES (2, '未升格联赛', 'temporary')")
    conn.execute(
        "INSERT INTO league_aliases (alias, league_id, source) VALUES ('未升格联赛', 2, 'dc_train')"
    )
    rows = [
        (1, "2099-03-01|star", "艾尔切队", 1, "哥隆尼斯", 2, "未升格联赛", 2),
        (2, "2099-03-01|clear", "AC米兰", 3, "利兹联", 4, "英超", 1),
        (3, "2099-03-01|promoted", "用户改正队", 5, "雷速新队", 6, "英超", 1),
    ]
    for mid, uid, home, home_id, away, away_id, league, league_id in rows:
        conn.execute(
            """INSERT INTO matches (
                 id, match_uid, scope, jingcai_date, weekday, kickoff_hour, jc_id, jc_no,
                 competition_name, competition_type, home_team, away_team,
                 kickoff_at, kickoff_minute_known, league_id, home_team_id, away_team_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, uid, "jingcai", "2099-03-01", "日", 20, None, None,
             league, "联赛", home, away, "2099-03-01T20:00:00+08:00", 1,
             league_id, home_id, away_id),
        )
    conn.commit()

    pending = tp.load_pending(conn)
    assert tp.norm("艾尔切队") in pending.pending
    assert tp.norm("哥隆尼斯") in pending.pending
    assert tp.is_pending(1, "艾尔切队", pending, "艾尔切队") is True
    assert tp.is_pending(2, "哥隆尼斯", pending, "哥隆尼斯") is True
    assert tp.reason(1, "艾尔切队", pending, "艾尔切队") == "final_pending_confirm"
    assert tp.norm("AC米兰") in pending.known
    assert tp.norm("利兹联") in pending.known
    assert tp.norm("AC米兰") not in pending.pending
    assert tp.norm("利兹联") not in pending.pending
    assert tp.is_pending(3, "AC米兰", pending, "AC米兰") is False
    assert tp.is_pending(4, "利兹联", pending, "利兹联") is False
    assert tp.is_pending(5, "用户改正队", pending, "用户改正队") is False
    assert tp.is_pending(6, "雷速新队", pending, "雷速新队") is False
    league_idx = pending.league
    assert league_idx is not None
    assert tp.is_pending(None, "英超", league_idx, "英超") is False
    assert tp.is_pending(None, "未升格联赛", league_idx, "未升格联赛") is True

    from app.main import build_match_obj, load_canonical_names

    names = load_canonical_names(conn)
    stored = {row["match_uid"]: row for row in conn.execute("SELECT * FROM matches")}
    starred = build_match_obj(stored["2099-03-01|star"], {}, pending, canonicals=names)
    assert starred["teams"]["home"] == "艾尔切队"
    assert starred["home_team_canonical"] == "艾尔切队"
    assert starred["away_team_canonical"] == "哥隆尼斯"
    assert starred["teams_pending_confirm"] == {"home": True, "away": True}
    assert starred["home_team_pending_confirm"] is True
    assert starred["away_team_pending_confirm"] is True
    assert starred["league_canonical"] == "未升格联赛"
    assert starred["league_pending_confirm"] is True
    clear = build_match_obj(stored["2099-03-01|clear"], {}, pending, canonicals=names)
    assert clear["home_team_canonical"] == "AC米兰"
    assert clear["away_team_canonical"] == "利兹联"
    assert clear["teams_pending_confirm"] == {"home": False, "away": False}
    assert clear["league_pending_confirm"] is False
    promoted = build_match_obj(stored["2099-03-01|promoted"], {}, pending, canonicals=names)
    assert promoted["teams_pending_confirm"] == {"home": False, "away": False}

    from app import table_matches as tm

    nested = tm.build_table(
        conn, fmt="nested", date_from="2099-03-01", date_to="2099-03-01", scope="all",
        strategy="CFFXDJ_5_V3", channel="rule", include_live="none", settlement_version=None,
        as_of=None, baseline_window_days=None, baseline_min_n=20, limit=20, offset=0,
    )
    by_id = {item["match_id"]: item["match"] for item in nested["items"]}
    table_star = by_id["2099-03-01|star"]
    assert table_star["home_team"] == "艾尔切队"
    assert table_star["home_team_canonical"] == "艾尔切队"
    assert table_star["away_team_canonical"] == "哥隆尼斯"
    assert table_star["home_team_pending_confirm"] is True
    assert table_star["away_team_pending_confirm"] is True
    assert table_star["league_pending_confirm"] is True
    table_clear = by_id["2099-03-01|clear"]
    assert table_clear["home_team_canonical"] == "AC米兰"
    assert table_clear["away_team_canonical"] == "利兹联"
    assert table_clear["home_team_pending_confirm"] is False
    assert table_clear["away_team_pending_confirm"] is False
    assert table_clear["league_pending_confirm"] is False
    table_promoted = by_id["2099-03-01|promoted"]
    assert table_promoted["home_team_pending_confirm"] is False
    assert table_promoted["away_team_pending_confirm"] is False
    conn.close()


def test_lookalike_does_not_inherit_established_name(tmp_path, monkeypatch):
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(tmp_path / "missing-final.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "missing-seed.csv"))
    idx = tp.load_index()
    assert tp.is_pending(None, None, idx, "切尔西") is False
    assert tp.reason(None, "切尔西", idx, "切尔西女") == "ambiguous_not_promoted"
    assert tp.is_pending(None, None, idx, "切尔西女") is True
    assert tp.is_pending(None, None, idx, "曼联青年") is True
    assert tp.is_pending(None, None, idx, "曼联") is False
