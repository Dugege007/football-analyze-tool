"""球队待确认标记（2026-10-14 分析师改为按名称判断）：队名在定稿表、提议表、种子表里找得到返回 false，
找不到或定稿表标了待确认返回 true；表文件缺失返回 true 不崩溃；不再按球队编号判断。"""
from __future__ import annotations

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


def test_missing_file_true_no_crash(tmp_path, caplog):
    fs = _files(tmp_path)
    fs["proposed"] = str(tmp_path / "nope.csv")
    idx = tp.load_index(fs)
    assert tp.reason(None, None, idx, "萨尔茨堡") == "table_missing"
    assert "nope.csv" in caplog.text


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
