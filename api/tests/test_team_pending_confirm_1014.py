"""球队待确认标记（2026-10-14 用户规则）：认不出的球队按来源原名写，接口输出 pending_confirm，前端悬停「待确认」。

正式库没有 teams.pending_confirm 列：从别名来源后缀 _pending_confirm 或导入包文件推断，否则默认 false；不改库结构、不写库。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import team_pending as tp  # noqa: E402
from app.main import build_match_obj  # noqa: E402

SCHEMA = """
CREATE TABLE teams (id INTEGER PRIMARY KEY AUTOINCREMENT, name_zh_canonical TEXT NOT NULL UNIQUE, note TEXT);
CREATE TABLE team_aliases (id INTEGER PRIMARY KEY AUTOINCREMENT, alias TEXT NOT NULL UNIQUE, team_id INTEGER NOT NULL, source TEXT);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.executemany("INSERT INTO teams(id, name_zh_canonical) VALUES (?, ?)",
                  [(1, "杜连斯"), (2, "济州SK FC"), (3, "古比斯")])
    c.executemany("INSERT INTO team_aliases(alias, team_id, source) VALUES (?, ?, ?)",
                  [("杜连斯", 1, "name_standard_20261014_pending_confirm"),
                   ("济州SK", 2, "name_standard_20261014"), ("古比斯", 3, "seed_from_matches")])
    return c


def test_default_false_without_marker(tmp_path):
    c = _conn()
    c.execute("UPDATE team_aliases SET source = 'name_standard_20261014' WHERE team_id = 1")
    p = tp.load_pending(c, file_path=str(tmp_path / "missing.csv"))
    assert p == (set(), set())
    assert tp.is_pending(1, "杜连斯", p) is False
    assert tp.is_pending(None, None, p) is True  # 未映射（没有球队编号）一律待确认


def test_alias_source_suffix_marks_team():
    c = _conn()
    p = tp.load_pending(c, file_path="")
    assert tp.is_pending(1, "杜连斯", p) is True
    assert tp.is_pending(2, "济州SK FC", p) is False
    assert tp.is_pending("1", None, p) is True


@pytest.mark.parametrize("header,row", [
    ("table,name_zh_canonical,alias,source,note,pending_confirm", "teams,古比斯,,,,true"),
    ("canonical,aliases,note,pending_confirm", "古比斯,KuPS,,true"),
])
def test_file_fallback_import_rows_or_teams_csv(tmp_path, header, row):
    f = tmp_path / "x.csv"
    f.write_text(header + "\n" + row + "\n", encoding="utf-8")
    p = tp.load_pending(_conn(), file_path=str(f))
    assert tp.is_pending(3, "古比斯", p) is True
    assert tp.is_pending(None, "古比斯", p) is True  # 库里还没有这支队时按常用名匹配


def test_file_fallback_ignores_leagues(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("table,name_zh_canonical,pending_confirm\nleagues,古比斯,true\n", encoding="utf-8")
    ids, names = tp.load_pending(_conn(), file_path=str(f))
    assert names == set() and 3 not in ids


def test_future_column_respected():
    c = _conn()
    c.execute("ALTER TABLE teams ADD COLUMN pending_confirm INTEGER DEFAULT 0")
    c.execute("UPDATE teams SET pending_confirm = 1 WHERE id = 3")
    p = tp.load_pending(c, file_path="")
    assert tp.is_pending(3, None, p) and tp.is_pending(1, None, p)


def test_load_pending_is_read_only():
    c = _conn()
    before = c.total_changes
    tp.load_pending(c, file_path="")
    assert c.total_changes == before


def _match_row(home_id, away_id):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    cols = dict(jc_id=None, jc_no=None, weekday=None, jingcai_date="2026-10-14", scope="jingcai",
                competition_name="葡超", competition_type=None, competition_stage=None, kickoff_hour=20,
                kickoff_at=None, kickoff_minute_known=0, home_team="杜连斯", away_team="卡萨皮亚",
                home_team_id=home_id, away_team_id=away_id, league_id=None)
    sql = "SELECT " + ", ".join(f"? AS {k}" for k in cols)
    return c.execute(sql, list(cols.values())).fetchone()


def test_match_detail_teams_pending_confirm():
    p = tp.load_pending(_conn(), file_path="")
    m = build_match_obj(_match_row(1, 2), {}, p)
    assert m["teams_pending_confirm"] == {"home": True, "away": False}
    m2 = build_match_obj(_match_row(None, None), {})
    assert m2["teams_pending_confirm"] == {"home": True, "away": True}
    m3 = build_match_obj(_match_row(5, None), {}, p)
    assert m3["teams_pending_confirm"] == {"home": False, "away": True}


def test_table_model_has_fields():
    from app import table_matches as tm
    fields = tm.MatchFields.model_fields if hasattr(tm, "MatchFields") else None
    src = Path(tm.__file__).read_text(encoding="utf-8")
    assert "home_team_pending_confirm: bool = False" in src
    assert '"away_team_pending_confirm": tp.is_pending(' in src
    if fields is not None:
        assert "home_team_pending_confirm" in fields


def test_unmapped_team_id_is_pending():
    empty = (set(), set())
    assert tp.is_pending(None, None, empty) is True
    assert tp.is_pending(None, "某队", empty) is True
    assert tp.is_pending("", None, empty) is True
    assert tp.is_pending(7, None, empty) is False
    assert tp.is_pending(None, None, None) is True
