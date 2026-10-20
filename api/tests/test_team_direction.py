"""对照表脚本：检查并替换写反的常用名与别名，不改比赛文本列。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import apply_team_direction as direction  # noqa: E402

CORRECTIONS = ROOT / "resources" / "name-standard" / "team-direction-corrections.csv"


def _paths(tmp_path: Path) -> list[str]:
    csv_path = tmp_path / "teams.csv"
    ts_path = tmp_path / "teamName.ts"
    seed_path = tmp_path / "seed.py"
    csv_path.write_text(
        "canonical,aliases,note,pending_confirm,promotion\n"
        "利雅得希拉尔,利雅得新月,,false,legacy_high_confidence\n"
        "金堡尔,坎布尔,,false,legacy_high_confidence\n",
        encoding="utf-8",
    )
    ts_path.write_text(
        'const TEAM_OTHER_NAMES = {\n'
        '  "利雅得希拉尔": ["利雅得新月"],\n'
        '  "金堡尔": ["坎布尔"],\n'
        '}\n',
        encoding="utf-8",
    )
    seed_path.write_text(
        "pairs = (\n"
        '        ("利雅得希拉尔", ["利雅得新月", "希拉尔", "利雅新月"]),\n'
        '        ("坎布尔", ["金堡尔", "坎布尔"]),\n'
        ")\n",
        encoding="utf-8",
    )
    return [
        "--corrections", str(CORRECTIONS),
        "--teams-csv", str(csv_path),
        "--team-name-ts", str(ts_path),
        "--tuple-seed", str(seed_path),
    ]


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY, name_zh_canonical TEXT NOT NULL UNIQUE)")
    conn.execute(
        "CREATE TABLE team_aliases (id INTEGER PRIMARY KEY, alias TEXT NOT NULL UNIQUE, team_id INTEGER NOT NULL, source TEXT)"
    )
    conn.execute("CREATE TABLE matches (id INTEGER PRIMARY KEY, home_team TEXT, away_team TEXT)")
    conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('利雅得希拉尔')")
    conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('金堡尔')")
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('利雅得新月', 1, 'legacy')")
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('坎布尔', 2, 'legacy')")
    conn.execute("INSERT INTO matches (home_team, away_team) VALUES ('利雅得希拉尔', '金堡尔')")
    conn.commit()
    conn.close()
    return path


def test_repository_files_match_corrections():
    assert direction.main([]) == 0


def test_check_does_not_write_and_apply_swaps_direction(tmp_path: Path):
    args = _paths(tmp_path)
    db_path = _db(tmp_path)
    csv_path = tmp_path / "teams.csv"
    before = csv_path.read_text(encoding="utf-8")
    assert direction.main([*args, "--db", str(db_path)]) == 1
    assert csv_path.read_text(encoding="utf-8") == before

    assert direction.main([*args, "--db", str(db_path), "--apply"]) == 0
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "利雅得新月,利雅得希拉尔,,false,legacy_high_confidence" in csv_text
    assert "坎布尔,金堡尔,,false,legacy_high_confidence" in csv_text
    ts_text = (tmp_path / "teamName.ts").read_text(encoding="utf-8")
    assert '"利雅得新月": ["利雅得希拉尔"]' in ts_text
    assert '"坎布尔": ["金堡尔"]' in ts_text
    seed_text = (tmp_path / "seed.py").read_text(encoding="utf-8")
    assert '("利雅得新月", ["利雅得希拉尔", "希拉尔", "利雅新月"])' in seed_text

    conn = sqlite3.connect(db_path)
    names = {row[0] for row in conn.execute("SELECT name_zh_canonical FROM teams")}
    aliases = {row[0] for row in conn.execute("SELECT alias FROM team_aliases")}
    promos = {row[0] for row in conn.execute("SELECT promotion_source FROM teams")}
    match = conn.execute("SELECT home_team, away_team FROM matches").fetchone()
    conn.close()
    assert names == {"利雅得新月", "坎布尔"}
    assert aliases == {"利雅得希拉尔", "金堡尔"}
    assert promos == {"legacy_high_confidence"}
    assert match == ("利雅得希拉尔", "金堡尔")
    assert direction.main([*args, "--db", str(db_path)]) == 0


def test_already_same_without_promotion_source_is_not_treated_as_promoted(tmp_path: Path):
    """常用名已经对齐、但升格来源为空时，检查不通过；--apply 只补来源，不改比赛文本。"""
    args = _paths(tmp_path)
    db_path = tmp_path / "same.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE teams (id INTEGER PRIMARY KEY, name_zh_canonical TEXT NOT NULL UNIQUE, promotion_source TEXT)"
    )
    conn.execute(
        "CREATE TABLE team_aliases (id INTEGER PRIMARY KEY, alias TEXT NOT NULL UNIQUE, team_id INTEGER NOT NULL, source TEXT)"
    )
    conn.execute("CREATE TABLE matches (id INTEGER PRIMARY KEY, home_team TEXT, away_team TEXT, competition_name TEXT)")
    conn.execute("INSERT INTO teams (name_zh_canonical, promotion_source) VALUES ('利雅得新月', NULL)")
    conn.execute("INSERT INTO teams (name_zh_canonical, promotion_source) VALUES ('坎布尔', NULL)")
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('利雅得希拉尔', 1, 'legacy')")
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('金堡尔', 2, 'legacy')")
    conn.execute("INSERT INTO matches (home_team, away_team, competition_name) VALUES ('利雅得希拉尔', '金堡尔', '沙特联')")
    conn.commit()
    conn.close()
    assert direction.main([*args, "--db", str(db_path)]) == 1
    assert direction.main([*args, "--db", str(db_path), "--apply"]) == 0
    conn = sqlite3.connect(db_path)
    promos = {row[0] for row in conn.execute("SELECT promotion_source FROM teams")}
    match = conn.execute("SELECT home_team, away_team, competition_name FROM matches").fetchone()
    conn.close()
    assert promos == {"legacy_high_confidence"}
    assert match == ("利雅得希拉尔", "金堡尔", "沙特联")
    assert direction.main([*args, "--db", str(db_path)]) == 0


def test_two_existing_teams_are_not_merged(tmp_path: Path):
    args = _paths(tmp_path)
    db_path = tmp_path / "split.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY, name_zh_canonical TEXT NOT NULL UNIQUE)")
    conn.execute(
        "CREATE TABLE team_aliases (id INTEGER PRIMARY KEY, alias TEXT NOT NULL UNIQUE, team_id INTEGER NOT NULL, source TEXT)"
    )
    conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('利雅得新月')")
    conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('利雅得希拉尔')")
    conn.execute("INSERT INTO teams (name_zh_canonical) VALUES ('坎布尔')")
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('金堡尔', 3, 'legacy')")
    conn.commit()
    conn.close()
    # 名册和前端仍是写反的，先改成正确方向，避免文件错误掩盖数据库冲突。
    assert direction.main([*args, "--apply"]) == 0
    assert direction.main([*args, "--db", str(db_path), "--apply"]) == 1
    conn = sqlite3.connect(db_path)
    names = [row[0] for row in conn.execute("SELECT name_zh_canonical FROM teams ORDER BY id")]
    conn.close()
    assert names == ["利雅得新月", "利雅得希拉尔", "坎布尔"]
