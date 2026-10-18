"""比赛身份编号回填：空槽填上，已有编号不覆盖，解析不到的名称进入待定池。

只用临时库。不打开仓库里的正式库。
"""
from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import import_lib  # noqa: E402
from backfill_match_entity_ids import run_backfill  # noqa: E402

SCRIPT = ROOT / "scripts" / "backfill_match_entity_ids.py"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE teams (
          id INTEGER PRIMARY KEY,
          name_zh_canonical TEXT NOT NULL UNIQUE
        );
        CREATE TABLE team_aliases (
          id INTEGER PRIMARY KEY,
          alias TEXT NOT NULL UNIQUE,
          team_id INTEGER NOT NULL REFERENCES teams(id),
          source TEXT
        );
        CREATE TABLE leagues (
          id INTEGER PRIMARY KEY,
          name_zh_canonical TEXT NOT NULL UNIQUE
        );
        CREATE TABLE league_aliases (
          id INTEGER PRIMARY KEY,
          alias TEXT NOT NULL UNIQUE,
          league_id INTEGER NOT NULL REFERENCES leagues(id),
          source TEXT
        );
        CREATE TABLE matches (
          id INTEGER PRIMARY KEY,
          jingcai_date TEXT,
          home_team TEXT,
          away_team TEXT,
          competition_name TEXT,
          home_team_id INTEGER,
          away_team_id INTEGER,
          league_id INTEGER,
          import_batch_id INTEGER,
          updated_at TEXT
        );
        """
    )
    return conn


def _sample(conn: sqlite3.Connection) -> int:
    """主队空槽可解析，客队已有编号，赛事解析不到。另有一场在日期窗外。"""
    united = conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('曼联')").lastrowid
    other = conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('已占用客队')").lastrowid
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('曼联', ?, 'test')",
        (united,),
    )
    conn.execute(
        """INSERT INTO matches (
             id, jingcai_date, home_team, away_team, competition_name,
             home_team_id, away_team_id, league_id
           ) VALUES (1, '2026-10-18', '曼联', '曼联', '没有这个联赛', NULL, ?, NULL)""",
        (other,),
    )
    conn.execute(
        """INSERT INTO matches (
             id, jingcai_date, home_team, away_team, competition_name
           ) VALUES (2, '2026-01-01', '曼联', '曼联', '英超')"""
    )
    conn.commit()
    return int(united)


def _ids(conn: sqlite3.Connection, match_id: int) -> tuple:
    row = conn.execute(
        "SELECT home_team_id, away_team_id, league_id FROM matches WHERE id = ?",
        (match_id,),
    ).fetchone()
    return (row["home_team_id"], row["away_team_id"], row["league_id"])


def test_dry_run_does_not_write(tmp_path):
    """默认预览只计数，不改编号，也不建待定池。"""
    path = tmp_path / "dry.db"
    conn = _connect(path)
    united = _sample(conn)
    before = _ids(conn, 1)
    teams_before = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    stats = run_backfill(conn, apply=False, write_pool=True, report_path=tmp_path / "dry.md")
    assert stats["mode"] == "dry-run"
    assert stats["create"] is False
    assert stats["scanned"] == 2
    assert stats["planned"]["home_team_id"] == 2
    assert stats["written"]["home_team_id"] == 0
    assert stats["pool_written"] == 0
    assert _ids(conn, 1) == before
    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == teams_before
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alias_pool_entry'"
    ).fetchone() is None
    text = (tmp_path / "dry.md").read_text(encoding="utf-8")
    assert "dry-run" in text
    assert "没有这个联赛" in text
    assert united == conn.execute("SELECT id FROM teams WHERE name_zh_canonical = '曼联'").fetchone()[0]


def test_apply_fills_null_slot_keeps_existing_and_pools_unresolved(tmp_path):
    """空槽写入别名编号；已有客队编号保持原值；解析不到的赛事进入待定池。"""
    path = tmp_path / "apply.db"
    conn = _connect(path)
    united = _sample(conn)
    other = conn.execute("SELECT away_team_id FROM matches WHERE id = 1").fetchone()[0]
    teams_before = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    leagues_before = conn.execute("SELECT COUNT(*) FROM leagues").fetchone()[0]
    stats = run_backfill(
        conn,
        apply=True,
        write_pool=True,
        date_from="2026-10-01",
        date_to="2026-10-18",
    )
    assert stats["scanned"] == 1
    assert stats["written"]["home_team_id"] == 1
    assert stats["written"]["away_team_id"] == 0
    assert stats["pool_written"] == 1
    conn.close()
    saved = sqlite3.connect(path)
    saved.row_factory = sqlite3.Row
    assert _ids(saved, 1) == (united, other, None)
    assert _ids(saved, 2) == (None, None, None)
    assert saved.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == teams_before
    assert saved.execute("SELECT COUNT(*) FROM leagues").fetchone()[0] == leagues_before
    pooled = saved.execute(
        "SELECT kind, raw_name, status FROM alias_pool_entry"
    ).fetchall()
    assert len(pooled) == 1
    assert pooled[0]["kind"] == "competition"
    assert pooled[0]["raw_name"] == "没有这个联赛"
    assert any(item["name"] == "没有这个联赛" for item in stats["unresolved"])
    saved.close()


def test_apply_without_pool_leaves_unresolved_out_of_pool(tmp_path):
    """只回填编号时，解析不到的名称不写待定池。"""
    path = tmp_path / "nopool.db"
    conn = _connect(path)
    united = _sample(conn)
    stats = run_backfill(conn, apply=True, write_pool=False)
    assert _ids(conn, 1)[0] == united
    assert stats["pool_written"] == 0
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alias_pool_entry'"
    ).fetchone() is None


def test_cli_dry_run_is_the_default(tmp_path):
    """命令行不带 --apply 时只读，帮助里能看到用法。"""
    path = tmp_path / "cli.db"
    conn = _connect(path)
    _sample(conn)
    conn.close()
    report = tmp_path / "cli.md"
    env = dict(os.environ)
    env["FAT_DISABLE_DOTENV"] = "1"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(path), "--only-null-slots", "--report", str(report)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert report.exists()
    check = sqlite3.connect(path)
    assert check.execute("SELECT home_team_id FROM matches WHERE id = 1").fetchone()[0] is None
    check.close()
    help_text = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert help_text.returncode == 0
    for flag in ("--db", "--dry-run", "--apply", "--date-from", "--date-to", "--write-pool", "--report"):
        assert flag in help_text.stdout


def test_resolve_create_false_does_not_insert_team(tmp_path):
    """只查不建：已知别名能解析，未知名称不新建球队。"""
    conn = _connect(tmp_path / "resolve.db")
    _sample(conn)
    assert import_lib.resolve_or_create_team(conn, "曼联", create=False) is not None
    assert import_lib.resolve_or_create_team(conn, "全新队名", create=False) is None
    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 2


def test_jc_lookup_resolves_without_creating(tmp_path):
    """竞彩场次构建的队名查找与回填共用只查不建。"""
    import build_jc_match_rows as jc

    conn = _connect(tmp_path / "jc.db")
    united = _sample(conn)
    found, canonical = jc.lookup(conn, "team", "曼联")
    assert (found, canonical) == (united, "曼联")
    assert jc.lookup(conn, "team", "全新队名") == (None, None)
    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 2


def test_script_does_not_seed_new_entities():
    """回填脚本不引用会新建实体的种子入口。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "seed_aliases_from_matches" not in src
    assert "create=True" not in src
    assert "CREATE_ENTITIES = False" in src


def test_fill_null_entity_ids_does_not_overwrite(tmp_path):
    """入库辅助函数只补空槽。"""
    path = tmp_path / "fill.db"
    conn = _connect(path)
    united = _sample(conn)
    other = conn.execute("SELECT away_team_id FROM matches WHERE id = 1").fetchone()[0]
    import_lib.fill_null_entity_ids(
        conn,
        1,
        home_team="曼联",
        away_team="曼联",
        competition_name="没有这个联赛",
        jingcai_date="2026-10-18",
        label="unit",
    )
    conn.commit()
    assert _ids(conn, 1) == (united, other, None)


def test_gate_warns_above_five_percent_and_refuses_mass_null():
    """超过 5% 告警；整批有名称的槽位全空时拒绝。"""
    partial = [{
        "jingcai_date": "2026-10-18",
        "home_team": "甲",
        "away_team": "乙",
        "competition_name": "丙",
        "home_team_id": 1,
        "away_team_id": 2,
        "league_id": None,
        "import_batch_id": 7,
    }]
    buf = io.StringIO()
    warned = import_lib.enforce_entity_id_gate(
        partial,
        label="unit",
        require_import_batch_id=True,
        fail_on_mass_null=True,
        stream=buf,
    )
    assert warned["alerts"]
    assert "33.3%" in buf.getvalue()

    blank = [{
        "jingcai_date": "2026-10-18",
        "home_team": "甲",
        "away_team": "乙",
        "competition_name": "丙",
        "home_team_id": None,
        "away_team_id": None,
        "league_id": None,
        "import_batch_id": 7,
    }]
    with pytest.raises(import_lib.EntityIdGateError, match="全部为空"):
        import_lib.enforce_entity_id_gate(blank, label="unit", fail_on_mass_null=True, stream=io.StringIO())


def test_assert_batch_refuses_when_import_batch_id_missing(tmp_path):
    """计划写入的场次都没有挂上批次号时拒绝提交。"""
    path = tmp_path / "batch.db"
    conn = _connect(path)
    _sample(conn)
    with pytest.raises(import_lib.EntityIdGateError, match="import_batch_id"):
        import_lib.assert_batch_entity_ids(conn, 9, 1, label="unit")
