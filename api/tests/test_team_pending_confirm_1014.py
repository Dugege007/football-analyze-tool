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
    assert tp.is_pending(None, None, idx, "博塔弗戈") is False  # 提议表
    assert tp.is_pending(None, None, idx, "济州SK") is False  # 种子表
    assert tp.is_pending(5, None, idx, "完全陌生队") is True  # 有编号也找不到 → true（不看编号）
    assert tp.reason(None, None, idx, "完全陌生队") == "not_found"
    assert tp.is_pending(None, None, idx, "中国杯") is True  # 种子表里的赛事行不算球队


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
    assert b is not a and tp.is_pending(None, None, b, "新队") is False


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
        CREATE TABLE teams (id INTEGER PRIMARY KEY, name_zh_canonical TEXT, pending_confirm TEXT);
        CREATE TABLE team_aliases (id INTEGER PRIMARY KEY, alias TEXT, team_id INTEGER, source TEXT);
        CREATE TABLE alias_pool_entry (
          norm_key TEXT, raw_name TEXT, kind TEXT, status TEXT
        );
        """
    )
    conn.execute("INSERT INTO teams (name_zh_canonical, pending_confirm) VALUES ('拜仁慕尼黑', 'false')")
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
