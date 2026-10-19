"""新名称并组的四条行为。

没有同场上下文时，整词前后缀不得猜挂。歧义只走 detect_ambiguity。
新建组的默认显示是这条新名，并保持待确认。没有常用名时显示仍不得为空。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import import_lib  # noqa: E402
from app import alias_pool  # noqa: E402
from app import team_pending as tp  # noqa: E402

_TIME_TEXT = "2026-10-19T20:00:00+08:00"


class _Clock:
    def __call__(self) -> str:
        return _TIME_TEXT


def _hide_external_rosters(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(tp.ENV_PATHS["final"], str(tmp_path / "missing-final.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["proposed"], str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv(tp.ENV_PATHS["seed"], str(tmp_path / "missing-seed.csv"))
    monkeypatch.setenv(tp.ENV_LEAGUE_FINAL, str(tmp_path / "missing-leagues.csv"))


def _pool() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE teams (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name_zh_canonical TEXT NOT NULL UNIQUE
        );
        CREATE TABLE team_aliases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          alias TEXT NOT NULL UNIQUE,
          team_id INTEGER NOT NULL REFERENCES teams(id),
          source TEXT
        );
        CREATE TABLE leagues (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name_zh_canonical TEXT NOT NULL UNIQUE
        );
        CREATE TABLE league_aliases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          alias TEXT NOT NULL UNIQUE,
          league_id INTEGER NOT NULL REFERENCES leagues(id),
          source TEXT
        );
        CREATE TABLE matches (
          id INTEGER PRIMARY KEY,
          home_team TEXT,
          away_team TEXT,
          competition_name TEXT,
          home_team_id INTEGER,
          away_team_id INTEGER,
          league_id INTEGER
        );
        """
    )
    alias_pool.ensure_schema(conn)
    return conn


def _entities(*, pending_column: bool, promotion_column: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    team_extra = ""
    league_extra = ""
    if pending_column:
        team_extra += ", pending_confirm TEXT"
        league_extra += ", pending_confirm TEXT"
    if promotion_column:
        team_extra += ", promotion TEXT"
        league_extra += ", promotion TEXT"
    conn.executescript(
        f"""
        CREATE TABLE teams (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name_zh_canonical TEXT NOT NULL UNIQUE
          {team_extra}
        );
        CREATE TABLE team_aliases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          alias TEXT NOT NULL UNIQUE,
          team_id INTEGER NOT NULL REFERENCES teams(id),
          source TEXT
        );
        CREATE TABLE leagues (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name_zh_canonical TEXT NOT NULL UNIQUE
          {league_extra}
        );
        CREATE TABLE league_aliases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          alias TEXT NOT NULL UNIQUE,
          league_id INTEGER NOT NULL REFERENCES leagues(id),
          source TEXT
        );
        """
    )
    return conn


def test_affix_without_same_match_context_does_not_guess_attach(monkeypatch):
    """弱模糊单独不得并组。没有同场上下文时，整词前缀不留下候选，也不写别名。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _pool()
    salzburg = conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('萨尔茨堡')").lastrowid
    bare = alias_pool.pool_observe(conn, "team", "FC 萨尔茨堡", "unit_test", "no-context")
    league_only = alias_pool.pool_observe(
        conn,
        "team",
        "AC 萨尔茨堡",
        "unit_test",
        "league-only",
        {"competition_name": "欧罗巴联赛"},
    )
    suffix = alias_pool.pool_observe(conn, "team", "萨尔茨堡FC", "unit_test", "suffix-fc")
    contained = alias_pool.pool_observe(conn, "team", "萨尔茨堡红牛", "unit_test", "substring")
    for result in (bare, league_only, suffix, contained):
        assert result["action"] == "pooled"
        assert result["standard_id"] is None
        assert result["candidate_id"] is None
        assert result["alias_inserted"] is False
    assert conn.execute("SELECT COUNT(*) FROM team_aliases").fetchone()[0] == 0
    assert conn.execute("SELECT id FROM teams").fetchone()[0] == salzburg


def test_affix_with_same_match_context_stays_candidate(monkeypatch):
    """对手已映射且有开赛时刻时，可以留下前后缀候选，仍然不得自动并组。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _pool()
    salzburg = int(conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('萨尔茨堡')").lastrowid)
    hinted = alias_pool.pool_observe(
        conn,
        "team",
        "FC 萨尔茨堡",
        "unit_test",
        "matches.id=9#home_team",
        {"opponent_mapped": True, "kickoff_at": "2026-10-19T19:00:00+08:00"},
    )
    assert hinted["action"] == "pooled"
    assert hinted["candidate_id"] == salzburg
    assert hinted["candidate_basis"] == "affix"
    assert hinted["standard_id"] is None
    assert hinted["alias_inserted"] is False
    assert conn.execute("SELECT 1 FROM team_aliases WHERE alias = 'FC 萨尔茨堡'").fetchone() is None
    women = alias_pool.pool_observe(
        conn,
        "team",
        "FC 曼联女",
        "unit_test",
        "women",
        {"opponent_mapped": True, "kickoff_at": "2026-10-19T19:00:00+08:00", "competition_name": "女超"},
    )
    assert women["action"] == "pooled"
    assert women["manual_reason"] == "gender"
    assert women["standard_id"] is None
    assert women["alias_inserted"] is False


def test_ambiguity_gate_is_detect_ambiguity(tmp_path, monkeypatch):
    """并入与否只看 detect_ambiguity 的原因代码，不另写一套性别或青年队规则。"""
    _hide_external_rosters(monkeypatch, tmp_path)
    conn = _entities(pending_column=True)
    men = int(conn.execute("INSERT INTO teams (name_zh_canonical, pending_confirm) VALUES ('切尔西', 'false')").lastrowid)
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('Al Ahli', ?, 'bad')",
        (men,),
    )
    blocked = import_lib.resolve_or_create_team(conn, "Al Ahli", source="import", create=True)
    assert blocked is None
    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 1
    women = import_lib.resolve_or_create_team(conn, "切尔西女", source="import", create=True)
    assert women is not None
    assert women != men
    row = conn.execute(
        "SELECT name_zh_canonical, pending_confirm FROM teams WHERE id = ?",
        (women,),
    ).fetchone()
    assert row["name_zh_canonical"] == "切尔西女"
    assert row["pending_confirm"] == "true"
    alias = conn.execute("SELECT team_id, source FROM team_aliases WHERE alias = '切尔西女'").fetchone()
    assert alias["team_id"] == women
    assert str(alias["source"]).endswith("_pending_confirm")


def test_new_group_displays_new_name_and_stays_pending(tmp_path, monkeypatch):
    """新建组的默认显示是这条新名，待确认为真，不得静默升成常用名。"""
    _hide_external_rosters(monkeypatch, tmp_path)
    conn = _entities(pending_column=True, promotion_column=True)
    team_id = import_lib.resolve_or_create_team(conn, "测试新组甲", source="import", create=True)
    league_id = import_lib.resolve_or_create_league(conn, "测试新赛事甲", source="import", create=True)
    assert team_id is not None and league_id is not None
    team = conn.execute(
        "SELECT name_zh_canonical, pending_confirm, promotion FROM teams WHERE id = ?",
        (team_id,),
    ).fetchone()
    league = conn.execute(
        "SELECT name_zh_canonical, pending_confirm, promotion FROM leagues WHERE id = ?",
        (league_id,),
    ).fetchone()
    assert team["name_zh_canonical"] == "测试新组甲"
    assert team["pending_confirm"] == "true"
    assert team["promotion"] == "temporary"
    assert league["name_zh_canonical"] == "测试新赛事甲"
    assert league["pending_confirm"] == "true"
    assert league["promotion"] == "temporary"
    for forbidden in ("leisutiyu", "雷速体育", "human_finalize", "legacy_high_confidence"):
        assert team["promotion"] != forbidden
        assert league["promotion"] != forbidden
    index = tp.load_pending(conn)
    assert tp.group_default_display(team["name_zh_canonical"], None) == "测试新组甲"
    assert tp.group_default_display(league["name_zh_canonical"], None) == "测试新赛事甲"
    assert tp.is_pending(team_id, team["name_zh_canonical"], index, None) is True
    assert tp.is_pending(league_id, league["name_zh_canonical"], index.league, None) is True
    known = import_lib.resolve_or_create_team(conn, "切尔西", source="import", create=True)
    known_row = conn.execute(
        "SELECT name_zh_canonical, pending_confirm, promotion FROM teams WHERE id = ?",
        (known,),
    ).fetchone()
    assert known_row["name_zh_canonical"] == "切尔西"
    assert known_row["pending_confirm"] == "false"
    assert known_row["promotion"] is None
    fresh = tp.load_pending(conn)
    assert tp.is_pending(known, "切尔西", fresh, "切尔西") is False


def test_new_group_without_pending_column_still_stars(tmp_path, monkeypatch):
    """正式表没有 pending_confirm 列时不改表结构，仍靠别名来源保持待确认。"""
    _hide_external_rosters(monkeypatch, tmp_path)
    conn = _entities(pending_column=False)
    team_id = import_lib.resolve_or_create_team(conn, "测试新组乙", source="seed_from_matches", create=True)
    row = conn.execute("SELECT name_zh_canonical FROM teams WHERE id = ?", (team_id,)).fetchone()
    alias = conn.execute("SELECT source FROM team_aliases WHERE alias = '测试新组乙'").fetchone()
    assert row["name_zh_canonical"] == "测试新组乙"
    assert str(alias["source"]).endswith("_pending_confirm")
    columns = {item[1] for item in conn.execute("PRAGMA table_info(teams)")}
    assert "pending_confirm" not in columns
    index = tp.load_pending(conn)
    assert tp.group_default_display(row["name_zh_canonical"], None) == "测试新组乙"
    assert tp.is_pending(team_id, row["name_zh_canonical"], index, None) is True


def test_group_without_preferred_name_keeps_display_and_star():
    """没有常用名时，组内默认显示不得为空，待确认仍为真。"""
    aliases = ("Brisbane Roar", "布里斯班狮吼")
    assert tp.group_default_display(None, None, aliases) == "布里斯班狮吼"
    assert tp.group_default_display("   ", "   ", aliases, entity_name=None) == "布里斯班狮吼"
    index = tp.NameIndex(known=set(), pending=set(), missing=[], authoritative=False)
    assert tp.is_pending(None, None, index, None) is True
    assert index.reason(None, None) == "name_absent"
