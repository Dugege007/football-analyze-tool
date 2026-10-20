"""七步入库闸：同场只加别名，外部取证只记线索，BSD 不升格。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import import_lib  # noqa: E402
from app import alias_pool  # noqa: E402
from app import name_resolve_gate as gate  # noqa: E402

_TIME = "2026-10-20T20:00:00+08:00"


class _Clock:
    def __call__(self) -> str:
        return "2026-10-20T21:00:00+08:00"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE teams (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name_zh_canonical TEXT NOT NULL UNIQUE,
          promotion_source TEXT,
          pending_confirm TEXT
        );
        CREATE TABLE team_aliases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          alias TEXT NOT NULL UNIQUE,
          team_id INTEGER NOT NULL,
          source TEXT
        );
        CREATE TABLE leagues (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name_zh_canonical TEXT NOT NULL UNIQUE,
          promotion_source TEXT,
          pending_confirm TEXT
        );
        CREATE TABLE league_aliases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          alias TEXT NOT NULL UNIQUE,
          league_id INTEGER NOT NULL,
          source TEXT
        );
        CREATE TABLE matches (
          id INTEGER PRIMARY KEY,
          home_team TEXT,
          away_team TEXT,
          competition_name TEXT,
          home_team_id INTEGER,
          away_team_id INTEGER,
          league_id INTEGER,
          kickoff_at TEXT
        );
        CREATE TABLE results (
          match_id INTEGER PRIMARY KEY,
          home_goals INTEGER,
          away_goals INTEGER
        );
        """
    )
    alias_pool.ensure_schema(conn)
    return conn


def test_gate_module_does_not_fetch_the_web():
    """第 5 步只提供钩子。模块源码不发起网页请求。"""
    source = Path(gate.__file__).read_text(encoding="utf-8")
    assert "urllib" not in source
    assert "httpx" not in source
    assert "requests" not in source
    assert "needs_external_evidence" in source


def test_exact_hit_adds_alias_only(monkeypatch):
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _conn()
    team_id = conn.execute(
        "INSERT INTO teams (name_zh_canonical, promotion_source, pending_confirm) VALUES ('曼联', 'temporary', 'true')"
    ).lastrowid
    conn.execute(
        "INSERT INTO matches (id, home_team, away_team, kickoff_at) VALUES (1, '曼彻斯特联', '利物浦', ?)",
        (_TIME,),
    )
    result = gate.admit(conn, "team", "曼联", "unit_test", "matches.id=1#home_team", allow_create=False)
    assert result["action"] == "auto"
    assert result["standard_id"] == team_id
    assert result["match_text_rewritten"] is False
    canonical = conn.execute("SELECT name_zh_canonical, promotion_source FROM teams WHERE id = ?", (team_id,)).fetchone()
    assert canonical["name_zh_canonical"] == "曼联"
    assert canonical["promotion_source"] == "temporary"
    text = conn.execute("SELECT home_team FROM matches WHERE id = 1").fetchone()[0]
    assert text == "曼彻斯特联"


def test_same_match_adds_alias_without_fuzzy_or_text_rewrite(monkeypatch):
    """另一侧已映射且开赛在十五分钟内时，只把新名加成别名。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _conn()
    home_id = conn.execute(
        "INSERT INTO teams (name_zh_canonical, promotion_source) VALUES ('曼联', 'temporary')"
    ).lastrowid
    away_id = conn.execute(
        "INSERT INTO teams (name_zh_canonical) VALUES ('利物浦')"
    ).lastrowid
    conn.execute(
        """
        INSERT INTO matches (id, home_team, away_team, home_team_id, away_team_id, kickoff_at)
        VALUES (7, '曼彻斯特联', '利物浦', ?, ?, ?)
        """,
        (home_id, away_id, _TIME),
    )
    context = {
        "kickoff_at": "2026-10-20T20:08:00+08:00",
        "slot": "home",
        "opponent_id": away_id,
        "opponent_mapped": True,
        "home_goals": 2,
        "away_goals": 1,
    }
    result = alias_pool.pool_observe(
        conn, "team", "红魔", "unit_test", "matches.id=7#home_team", context
    )
    assert result["action"] == "auto"
    assert result["criterion"] == "same_match_multi_evidence"
    assert result["standard_id"] == home_id
    alias = conn.execute("SELECT team_id, source FROM team_aliases WHERE alias = '红魔'").fetchone()
    assert alias["team_id"] == home_id
    assert "same_match" in alias["source"]
    canonical = conn.execute("SELECT name_zh_canonical, promotion_source FROM teams WHERE id = ?", (home_id,)).fetchone()
    assert canonical["name_zh_canonical"] == "曼联"
    assert canonical["promotion_source"] == "temporary"
    assert conn.execute("SELECT home_team FROM matches WHERE id = 7").fetchone()[0] == "曼彻斯特联"
    stored = conn.execute("SELECT context_json, extras FROM alias_pool_sighting, alias_pool_entry").fetchone()
    # 比分不得进入待定池。这次是自动映射，出现记录可以为空；线索里也不能有比分。
    assert stored is None or "2" not in (stored[0] or "")


def test_score_alone_does_not_merge(monkeypatch):
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _conn()
    conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('曼联')")
    conn.execute(
        "INSERT INTO matches (id, home_team, home_team_id, kickoff_at) VALUES (3, '红魔', 1, ?)",
        (_TIME,),
    )
    conn.execute("INSERT INTO results (match_id, home_goals, away_goals) VALUES (3, 1, 0)")
    verdict = gate.evaluate(
        conn,
        "team",
        "红魔",
        {"kickoff_at": _TIME, "slot": "home", "home_goals": 1, "away_goals": 0, "match_id": 3},
        source_ref="matches.id=3#home_team",
    )
    assert verdict["action"] != "merge_alias"
    assert conn.execute("SELECT COUNT(*) FROM team_aliases").fetchone()[0] == 0


def test_gender_conflict_does_not_merge(monkeypatch):
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _conn()
    team_id = conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('切尔西')").lastrowid
    away_id = conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('阿森纳')").lastrowid
    conn.execute(
        """
        INSERT INTO matches (id, home_team, home_team_id, away_team_id, kickoff_at)
        VALUES (4, '切尔西女', ?, ?, ?)
        """,
        (team_id, away_id, _TIME),
    )
    result = alias_pool.pool_observe(
        conn,
        "team",
        "切尔西女",
        "unit_test",
        "matches.id=4#home_team",
        {"kickoff_at": _TIME, "opponent_mapped": True, "opponent_id": away_id, "slot": "home"},
    )
    assert result["action"] == "pooled"
    assert result["manual_reason"] == "gender"
    assert conn.execute("SELECT COUNT(*) FROM team_aliases").fetchone()[0] == 0


def test_weak_substring_without_filled_slot_stays_pending(monkeypatch):
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _conn()
    conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('萨尔茨堡')")
    result = alias_pool.pool_observe(conn, "team", "萨尔茨堡红牛", "unit_test", "substring-only")
    assert result["action"] == "pooled"
    assert result["standard_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM team_aliases").fetchone()[0] == 0


def test_external_evidence_is_recorded_not_merged(monkeypatch):
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _conn()
    result = alias_pool.pool_observe(
        conn,
        "team",
        "完全未知队",
        "unit_test",
        "matches.id=9#home_team",
        {"kickoff_at": _TIME, "opponent_name": "利物浦", "opponent_mapped": False},
    )
    assert result["action"] == "pooled"
    assert result["manual_reason"] == "needs_external_evidence"
    assert result["standard_id"] is None
    extras = conn.execute("SELECT extras FROM alias_pool_entry").fetchone()[0]
    assert "利物浦" in extras
    assert "needs_external_evidence" in (result["manual_reason"] or "")
    sighting = conn.execute("SELECT context_json FROM alias_pool_sighting").fetchone()[0]
    assert "home_goals" not in sighting


def test_league_side_adds_alias_only(monkeypatch):
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _conn()
    league_id = conn.execute(
        "INSERT INTO leagues (name_zh_canonical, promotion_source) VALUES ('英超', 'temporary')"
    ).lastrowid
    result = alias_pool.pool_observe(
        conn,
        "competition",
        "英格兰超级联赛",
        "unit_test",
        "matches.id=5#competition_name",
        {"home_team_id": 1, "away_team_id": 2, "league_id": league_id},
    )
    assert result["action"] == "auto"
    assert result["criterion"] == "league_side_mapped"
    alias = conn.execute(
        "SELECT league_id FROM league_aliases WHERE alias = '英格兰超级联赛'"
    ).fetchone()
    assert alias["league_id"] == league_id
    row = conn.execute("SELECT name_zh_canonical, promotion_source FROM leagues WHERE id = ?", (league_id,)).fetchone()
    assert row["name_zh_canonical"] == "英超"
    assert row["promotion_source"] == "temporary"


def test_similar_league_name_without_ids_does_not_merge(monkeypatch):
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _conn()
    conn.execute("INSERT INTO leagues (name_zh_canonical) VALUES ('英超')")
    verdict = gate.evaluate(conn, "competition", "英超联赛", {"competition_name": "英超联赛"})
    assert verdict["action"] != "merge_alias"


def test_allow_create_keeps_new_group_pending(tmp_path, monkeypatch):
    """允许新建时，默认显示是新名，待确认为真，不得写成已升格。"""
    monkeypatch.setenv("APP_TEAM_FINAL_FILE", str(tmp_path / "missing.csv"))
    monkeypatch.setenv("APP_TEAM_PROPOSED_FILE", str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv("APP_TEAM_SEED_FILE", str(tmp_path / "missing-seed.csv"))
    monkeypatch.setenv("APP_LEAGUE_FINAL_FILE", str(tmp_path / "missing-leagues.csv"))
    conn = _conn()
    result = gate.admit(conn, "team", "闸新建队", "import", "manual-ref", allow_create=True)
    assert result["action"] == "create_group"
    assert result["match_text_rewritten"] is False
    row = conn.execute(
        "SELECT name_zh_canonical, pending_confirm, promotion_source FROM teams WHERE name_zh_canonical = '闸新建队'"
    ).fetchone()
    assert row["pending_confirm"] == "true"
    assert row["promotion_source"] in (None, "")
    for forbidden in ("leisutiyu", "雷速体育", "human_finalize", "legacy_high_confidence"):
        assert row["promotion_source"] != forbidden


def test_bsd_create_does_not_insert_promoted_team(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_TEAM_FINAL_FILE", str(tmp_path / "missing.csv"))
    monkeypatch.setenv("APP_TEAM_PROPOSED_FILE", str(tmp_path / "missing-proposed.csv"))
    monkeypatch.setenv("APP_TEAM_SEED_FILE", str(tmp_path / "missing-seed.csv"))
    monkeypatch.setenv("APP_LEAGUE_FINAL_FILE", str(tmp_path / "missing-leagues.csv"))
    conn = _conn()
    created = import_lib.resolve_or_create_team(conn, "BSD新队", source="bsd_event_map", create=True)
    assert created is None
    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0
    observed = gate.admit(
        conn,
        "team",
        "BSD新队",
        "bsd_unresolved",
        "matches.id=8#away_team",
        {"kickoff_at": _TIME, "opponent_name": "曼联"},
        allow_create=True,
    )
    assert observed["action"] == "pooled"
    assert conn.execute(
        "SELECT promotion_source FROM teams WHERE name_zh_canonical = 'BSD新队'"
    ).fetchone() is None


def test_open_slot_observe_does_not_rewrite_match_text(monkeypatch):
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _conn()
    conn.execute(
        "INSERT INTO matches (id, home_team, away_team, competition_name, kickoff_at) VALUES (11, '未知主', '未知客', '未知杯', ?)",
        (_TIME,),
    )
    report = gate.observe_open_slots(conn, [11], source="result_ingest")
    assert report["match_text_rewritten"] is False
    assert report["errors"] == []
    row = conn.execute(
        "SELECT home_team, away_team, competition_name, home_team_id FROM matches WHERE id = 11"
    ).fetchone()
    assert row["home_team"] == "未知主"
    assert row["away_team"] == "未知客"
    assert row["competition_name"] == "未知杯"
    assert row["home_team_id"] is None
    reasons = {
        item[0]
        for item in conn.execute("SELECT manual_reason FROM alias_pool_entry")
    }
    assert "needs_external_evidence" in reasons
