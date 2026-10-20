"""雷速升格默认干跑，写入时只改映射表和名称包。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import promote_leisuti_names as leisuti  # noqa: E402


def _db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE teams (
          id INTEGER PRIMARY KEY,
          name_zh_canonical TEXT NOT NULL UNIQUE
        );
        CREATE TABLE team_aliases (
          id INTEGER PRIMARY KEY,
          alias TEXT NOT NULL UNIQUE,
          team_id INTEGER NOT NULL,
          source TEXT
        );
        CREATE TABLE leagues (
          id INTEGER PRIMARY KEY,
          name_zh_canonical TEXT NOT NULL UNIQUE
        );
        CREATE TABLE league_aliases (
          id INTEGER PRIMARY KEY,
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
        INSERT INTO teams (id, name_zh_canonical) VALUES (18, '利雅得希拉尔');
        INSERT INTO team_aliases (alias, team_id, source) VALUES ('希拉尔', 18, 'import');
        INSERT INTO matches (id, home_team, away_team, home_team_id, away_team_id, kickoff_at)
        VALUES (1, '利雅得希拉尔', '利物浦', 18, NULL, '2026-10-20T20:00:00+08:00');
        """
    )
    conn.commit()
    conn.close()


def test_dry_run_writes_nothing(tmp_path: Path):
    db_path = tmp_path / "app.db"
    _db(db_path)
    teams = tmp_path / "teams.csv"
    competitions = tmp_path / "competitions.csv"
    teams.write_text(
        "canonical,aliases,note,pending_confirm,promotion\n利雅得希拉尔,希拉尔,,false,\n",
        encoding="utf-8",
    )
    competitions.write_text(
        "canonical,aliases,note,pending_confirm,promotion\n",
        encoding="utf-8",
    )
    incoming = tmp_path / "leisuti.csv"
    incoming.write_text(
        "kind,entity_id,preferred_name\nteam,18,利雅得新月\n",
        encoding="utf-8",
    )
    audit = tmp_path / "audit.json"
    before = teams.read_text(encoding="utf-8")
    code = leisuti.main([
        "--input", str(incoming),
        "--db", str(db_path),
        "--teams-csv", str(teams),
        "--competitions-csv", str(competitions),
        "--audit", str(audit),
    ])
    assert code == 0
    report = json.loads(audit.read_text(encoding="utf-8"))
    assert report["mode"] == "dry-run"
    assert report["match_text_rewritten"] is False
    assert report["items"][0]["action"] == "promote"
    assert report["items"][0]["old"] == "利雅得希拉尔"
    assert report["items"][0]["new"] == "利雅得新月"
    assert teams.read_text(encoding="utf-8") == before
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT name_zh_canonical FROM teams WHERE id = 18").fetchone()[0] == "利雅得希拉尔"
    assert conn.execute("SELECT home_team FROM matches WHERE id = 1").fetchone()[0] == "利雅得希拉尔"
    conn.close()


def test_apply_updates_mapping_and_pack_only(tmp_path: Path):
    db_path = tmp_path / "app.db"
    _db(db_path)
    teams = tmp_path / "teams.csv"
    competitions = tmp_path / "competitions.csv"
    teams.write_text(
        "canonical,aliases,note,pending_confirm,promotion\n利雅得希拉尔,希拉尔,,false,\n",
        encoding="utf-8",
    )
    competitions.write_text("canonical,aliases,note,pending_confirm,promotion\n", encoding="utf-8")
    incoming = tmp_path / "leisuti.json"
    incoming.write_text(
        json.dumps([
            {
                "kind": "team",
                "preferred_name": "利雅得新月",
                "side": "home",
                "kickoff_at": "2026-10-20T20:10:00+08:00",
                "home": "利雅得希拉尔",
                "away": "利物浦",
            }
        ]),
        encoding="utf-8",
    )
    audit = tmp_path / "apply.json"
    assert leisuti.main([
        "--input", str(incoming),
        "--db", str(db_path),
        "--apply",
        "--batch", "leisuti-test",
        "--teams-csv", str(teams),
        "--competitions-csv", str(competitions),
        "--audit", str(audit),
    ]) == 0
    report = json.loads(audit.read_text(encoding="utf-8"))
    assert report["mode"] == "apply"
    assert report["match_text_rewritten"] is False
    assert report["items"][0]["action"] == "promote"
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT name_zh_canonical, promotion_source, pending_confirm FROM teams WHERE id = 18"
    ).fetchone()
    alias = conn.execute(
        "SELECT team_id, source FROM team_aliases WHERE alias = '利雅得希拉尔'"
    ).fetchone()
    match = conn.execute("SELECT home_team, away_team FROM matches WHERE id = 1").fetchone()
    conn.close()
    assert row == ("利雅得新月", "leisutiyu", "false")
    assert alias[0] == 18
    assert alias[1] == "leisutiyu:leisuti-test"
    assert match == ("利雅得希拉尔", "利物浦")
    pack = teams.read_text(encoding="utf-8")
    assert "利雅得新月" in pack
    assert "利雅得希拉尔" in pack
    assert "leisutiyu" in pack
    assert competitions.read_text(encoding="utf-8").strip() == "canonical,aliases,note,pending_confirm,promotion"


def test_ambiguous_fixture_is_skipped(tmp_path: Path):
    db_path = tmp_path / "app.db"
    _db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO matches (id, home_team, away_team, home_team_id, kickoff_at)
        VALUES (2, '利雅得希拉尔', '利物浦', 18, '2026-10-20T20:05:00+08:00')
        """
    )
    conn.commit()
    conn.close()
    teams = tmp_path / "teams.csv"
    competitions = tmp_path / "competitions.csv"
    teams.write_text("canonical,aliases,note,pending_confirm,promotion\n", encoding="utf-8")
    competitions.write_text("canonical,aliases,note,pending_confirm,promotion\n", encoding="utf-8")
    incoming = tmp_path / "leisuti.csv"
    incoming.write_text(
        "preferred_name,side,kickoff_at,home,away\n利雅得新月,home,2026-10-20T20:00:00+08:00,利雅得希拉尔,利物浦\n",
        encoding="utf-8",
    )
    audit = tmp_path / "skip.json"
    leisuti.main([
        "--input", str(incoming),
        "--db", str(db_path),
        "--apply",
        "--teams-csv", str(teams),
        "--competitions-csv", str(competitions),
        "--audit", str(audit),
    ])
    report = json.loads(audit.read_text(encoding="utf-8"))
    assert report["items"][0]["action"] == "skip"
    assert report["items"][0]["reason"] == "ambiguous_match"
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT name_zh_canonical FROM teams WHERE id = 18").fetchone()[0] == "利雅得希拉尔"
    conn.close()
