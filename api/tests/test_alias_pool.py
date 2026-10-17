"""待定池核心库的内存数据库测试。

覆盖重复上报不增加次数、规范化键与球队待确认模块一致、歧义标记不自动映射、
宽松键对应两个标准名时不自动映射、唯一别名宽松键会自动映射、撤销后不再自动映射，
以及建表语句可以重复执行。正式库写入保护用临时文件验证，不打开仓库里的正式库。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import alias_pool  # noqa: E402
from app import formal_db  # noqa: E402
from app import team_pending  # noqa: E402
from app.db import MIGRATION_PATHS  # noqa: E402

_TIME_TEXT = "2026-10-17T10:00:00+08:00"


class _Clock:
    """测试用的固定北京时间。需要观察时间是否被改写时，再把 value 换成后一个时刻。"""

    def __init__(self) -> None:
        self.value = _TIME_TEXT

    def __call__(self) -> str:
        return self.value


def _connect() -> sqlite3.Connection:
    """建立只存在于内存中的库，并放入别名解析会读到的空表。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE teams (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name_zh_canonical TEXT NOT NULL UNIQUE,
          created_at TEXT,
          note TEXT
        );
        CREATE TABLE team_aliases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          alias TEXT NOT NULL UNIQUE,
          team_id INTEGER NOT NULL REFERENCES teams(id),
          source TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE leagues (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name_zh_canonical TEXT NOT NULL UNIQUE,
          created_at TEXT,
          note TEXT
        );
        CREATE TABLE league_aliases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          alias TEXT NOT NULL UNIQUE,
          league_id INTEGER NOT NULL REFERENCES leagues(id),
          source TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
        CREATE TABLE predictions (
          id INTEGER PRIMARY KEY,
          match_id INTEGER,
          strategy TEXT,
          direction TEXT,
          rationale_json TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO predictions (id, match_id, strategy, direction, rationale_json) VALUES (1, 7, 'S1', 'home', '冻结预测')"
    )
    alias_pool.ensure_schema(conn)
    return conn


def _team(conn: sqlite3.Connection, name: str) -> int:
    cursor = conn.execute("INSERT INTO teams (name_zh_canonical) VALUES (?)", (name,))
    return int(cursor.lastrowid)


def _league(conn: sqlite3.Connection, name: str) -> int:
    cursor = conn.execute("INSERT INTO leagues (name_zh_canonical) VALUES (?)", (name,))
    return int(cursor.lastrowid)


def _predictions(conn: sqlite3.Connection) -> list[tuple]:
    return [tuple(row) for row in conn.execute("SELECT * FROM predictions ORDER BY id")]


def _audit_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM alias_mapping_audit").fetchone()[0])


def _alias_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM team_aliases").fetchone()[0])


def test_norm_key_matches_team_pending_norm():
    """同一批样例上，规范化键必须与球队待确认模块的 norm 完全相同。"""
    samples = [
        None,
        "",
        "   ",
        "  ＡＣ　米兰 ",
        "FC 萨尔斯堡",
        "ＲＢ　Ｓａｌｚｂｕｒｇ",
        "曼联\n",
        "U21",
        "全角　空格",
        "Mixed Case FC",
        "Ａ．Ｃ．米兰",
        "圣·保罗",
        "曼联（Ｗ）",
        "ac-米兰",
    ]
    for sample in samples:
        assert alias_pool.norm_key(sample) == team_pending.norm(sample)


def test_loose_key_only_deletes_listed_punctuation():
    """宽松键只删除空白和方案点名的标点，不去掉前后缀。"""
    assert alias_pool.loose_key("ＡＣ．米兰") == alias_pool.loose_key("AC 米兰")
    assert alias_pool.loose_key("ac-米兰") == alias_pool.loose_key("A.C.米兰")
    assert alias_pool.loose_key("圣·保罗") == alias_pool.loose_key("圣・保罗") == "圣保罗"
    assert alias_pool.loose_key("FC 萨尔斯堡") == "fc萨尔斯堡"
    assert alias_pool.loose_key("FC 萨尔斯堡") != alias_pool.loose_key("萨尔茨堡")


def test_ambiguity_markers_are_detected_and_plain_names_are_not():
    """方案点名的标记要识别出来。嵌入在普通单词里的字母片段不能误判。"""
    assert alias_pool.detect_ambiguity("拜仁慕尼黑女足") == "gender"
    assert alias_pool.detect_ambiguity("皇家马德里女子") == "gender"
    assert alias_pool.detect_ambiguity("切尔西女") == "gender"
    assert alias_pool.detect_ambiguity("曼联（Ｗ）") == "gender"
    assert alias_pool.detect_ambiguity("切尔西 Ladies") == "gender"
    assert alias_pool.detect_ambiguity("曼联U21") == "age_group"
    assert alias_pool.detect_ambiguity("曼联 U-21") == "age_group"
    assert alias_pool.detect_ambiguity("曼联U22") == "age_u22"
    assert alias_pool.detect_ambiguity("曼联 U-22") == "age_u22"
    assert alias_pool.detect_ambiguity("曼联U24") == "age_group"
    assert alias_pool.detect_ambiguity("曼联青年队") == "age_group"
    assert alias_pool.detect_ambiguity("Sub-20") == "age_group"
    assert alias_pool.detect_ambiguity("巴萨B队") == "reserve_team"
    assert alias_pool.detect_ambiguity("巴萨B 队") == "reserve_team"
    assert alias_pool.detect_ambiguity("Barcelona B") == "reserve_team"
    assert alias_pool.detect_ambiguity("Castilla") == "reserve_team"
    assert alias_pool.detect_ambiguity("Real Madrid Castilla") == "reserve_team"
    assert alias_pool.detect_ambiguity("多特蒙德II") == "reserve_team"
    assert alias_pool.detect_ambiguity("多特蒙德 II") == "reserve_team"
    assert alias_pool.detect_ambiguity("曼联2") == "reserve_team"
    assert alias_pool.detect_ambiguity("青年Reserves") == "age_group"
    assert alias_pool.detect_ambiguity("阿赫利") == "same_name_other_team"
    assert alias_pool.detect_ambiguity("沙特阿赫利") == "same_name_other_team"
    assert alias_pool.detect_ambiguity("韩国") == "same_name_other_team"
    assert alias_pool.detect_ambiguity("大韩民国") == "same_name_other_team"
    assert alias_pool.detect_ambiguity("Al Ahly") == "same_name_other_team"
    assert alias_pool.detect_ambiguity("Al Ahli") == "same_name_other_team"
    assert alias_pool.detect_ambiguity("Al-Ahli") == "same_name_other_team"
    assert alias_pool.detect_ambiguity("曼联 w") == "gender"
    assert alias_pool.detect_ambiguity("曼联") is None
    assert alias_pool.detect_ambiguity("Brescia") is None
    assert alias_pool.detect_ambiguity("VIII") is None
    assert alias_pool.detect_ambiguity("日本J2联赛") is None
    assert alias_pool.detect_ambiguity("亚足联冠军精英联赛二") is None
    assert alias_pool.detect_ambiguity("欧联杯") is None


def test_repeated_report_does_not_increase_count(monkeypatch):
    """同一个来源位置重复上报时，出现次数和最近时间都保持不变。换一个位置才会增加。"""
    clock = _Clock()
    monkeypatch.setattr(alias_pool, "now_beijing_text", clock)
    conn = _connect()
    frozen = _predictions(conn)
    first = alias_pool.pool_observe(
        conn,
        "team",
        "科特布斯",
        "unit_test",
        "matches.id=338#home_team",
        {"competition_name": "德国乙级联赛", "home_goals": 2, "score": "1-0", "odds": "1.8"},
    )
    assert first["action"] == "pooled"
    assert first["seen_count"] == 1
    assert first["status"] == "pending"
    clock.value = "2026-10-17T11:00:00+08:00"
    second = alias_pool.pool_observe(
        conn,
        "team",
        "科特布斯",
        "unit_test",
        "matches.id=338#home_team",
        {"competition_name": "德国乙级联赛"},
    )
    assert second["repeated"] is True
    assert second["seen_count"] == 1
    row = conn.execute("SELECT seen_count, first_seen_at, last_seen_at, raw_name FROM alias_pool_entry").fetchone()
    assert row["seen_count"] == 1
    assert row["first_seen_at"] == _TIME_TEXT
    assert row["last_seen_at"] == _TIME_TEXT
    assert row["raw_name"] == "科特布斯"
    assert conn.execute("SELECT COUNT(*) FROM alias_pool_sighting").fetchone()[0] == 1
    stored = json.loads(conn.execute("SELECT context_json FROM alias_pool_sighting").fetchone()[0])
    assert stored == {"competition_name": "德国乙级联赛"}
    assert "score" not in stored
    assert "home_goals" not in stored
    third = alias_pool.pool_observe(
        conn, "team", "  科特布斯  ", "unit_test", "matches.id=339#away_team", {"competition_name": "德国乙级联赛"}
    )
    assert third["seen_count"] == 2
    assert conn.execute("SELECT raw_name FROM alias_pool_entry").fetchone()[0] == "科特布斯"
    assert _audit_count(conn) == 0
    assert _predictions(conn) == frozen


def test_ambiguity_markers_never_auto_map(monkeypatch):
    """名称本身或匹配到的标准名带有歧义标记时，即使文字完全相同也不自动映射。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _connect()
    samples = [
        ("拜仁慕尼黑女足", "gender"),
        ("曼联U21", "age_group"),
        ("曼联U22", "age_u22"),
        ("巴萨B队", "reserve_team"),
        ("Barcelona B", "reserve_team"),
        ("多特蒙德II", "reserve_team"),
        ("Sub-20", "age_group"),
        ("沙特阿赫利", "same_name_other_team"),
    ]
    for name, reason in samples:
        _team(conn, name)
        result = alias_pool.pool_observe(conn, "team", name, "unit_test", f"matches.id={name}#home_team")
        assert result["action"] == "pooled", name
        assert result["status"] == "ambiguous", name
        assert result["manual_reason"] == reason, name
    parent = _team(conn, "曼联(W)")
    blocked = alias_pool.pool_observe(conn, "team", "曼联W", "unit_test", "loose-w")
    assert blocked["action"] == "pooled"
    assert blocked["status"] == "ambiguous"
    assert blocked["manual_reason"] == "gender"
    assert blocked["candidate_id"] == parent
    homonym = _team(conn, "阿赫利")
    ahly = alias_pool.pool_observe(conn, "team", "阿赫利", "unit_test", "ahly")
    assert ahly["action"] == "pooled"
    assert ahly["manual_reason"] == "same_name_other_team"
    assert ahly["candidate_id"] == homonym
    assert _audit_count(conn) == 0
    assert _alias_count(conn) == 0


def test_two_standard_names_with_same_loose_key_do_not_auto_map(monkeypatch):
    """宽松键同时对应两个标准名时进入待定池，不写别名，也不写审计。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _connect()
    frozen = _predictions(conn)
    _team(conn, "AC米兰")
    _team(conn, "A.C.米兰")
    conn.execute(
        "INSERT INTO matches (id, home_team, competition_name) VALUES (1, 'ＡＣ．米兰', '欧洲冠军联赛')"
    )
    result = alias_pool.pool_observe(
        conn,
        "team",
        "ＡＣ．米兰",
        "unit_test",
        "matches.id=1#home_team",
        {"competition_name": "欧洲冠军联赛"},
    )
    assert result["action"] == "pooled"
    assert result["status"] == "ambiguous"
    assert result["manual_reason"] == "multi_candidate"
    assert result["seen_count"] == 1
    assert result["candidate_id"] is None
    assert "AC米兰" in result["candidate_reason"]
    assert "A.C.米兰" in result["candidate_reason"]
    assert conn.execute("SELECT home_team_id FROM matches WHERE id = 1").fetchone()[0] is None
    assert _alias_count(conn) == 0
    assert _audit_count(conn) == 0
    assert _predictions(conn) == frozen


def test_level1_and_level2_auto_map_only_identity_columns(monkeypatch):
    """等级 1 和等级 2 都会自动生效，只回填当前场次的身份编号，不改预测。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _connect()
    frozen = _predictions(conn)
    united = _team(conn, "曼联")
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('曼彻斯特联', ?, 'seed')",
        (united,),
    )
    milan = _team(conn, "AC米兰")
    paulo = _team(conn, "圣保罗")
    conn.execute(
        "INSERT INTO matches (id, home_team, away_team, competition_name, away_team_id) VALUES (7, '曼彻斯特联', '客队', '英超', 9)"
    )
    conn.execute("INSERT INTO matches (id, home_team, competition_name) VALUES (8, 'AC 米兰', '欧冠')")
    conn.execute("INSERT INTO matches (id, away_team, competition_name) VALUES (9, '圣·保罗', '巴甲')")
    level1 = alias_pool.pool_observe(conn, "team", "曼彻斯特联", "unit_test", "matches.id=7#home_team")
    assert level1["action"] == "auto"
    assert level1["criterion"] == "known_alias_exact"
    assert level1["confidence"] == "level1_known_alias"
    assert level1["standard_id"] == united
    assert level1["alias_inserted"] is False
    assert level1["entry_id"] is None
    assert level1["match_updated"] is True
    level2 = alias_pool.pool_observe(
        conn, "team", "AC 米兰", "unit_test", "matches.id=8#home_team", {"competition_name": "欧冠"}
    )
    assert level2["action"] == "auto"
    assert level2["criterion"] == "loose_key_equal"
    assert level2["confidence"] == "level2_equal_after_cleaning"
    assert level2["standard_id"] == milan
    assert level2["alias_inserted"] is True
    dotted = alias_pool.pool_observe(conn, "team", "圣·保罗", "unit_test", "matches.id=9#away_team")
    assert dotted["action"] == "auto"
    assert dotted["standard_id"] == paulo
    home = conn.execute("SELECT home_team_id, away_team_id, league_id FROM matches WHERE id = 7").fetchone()
    assert home["home_team_id"] == united
    assert home["away_team_id"] == 9
    assert home["league_id"] is None
    assert conn.execute("SELECT home_team_id FROM matches WHERE id = 8").fetchone()[0] == milan
    assert conn.execute("SELECT away_team_id FROM matches WHERE id = 9").fetchone()[0] == paulo
    audit = conn.execute(
        "SELECT mode, actor, effective_at, created_at, alias_row_table FROM alias_mapping_audit WHERE id = ?",
        (level2["audit_id"],),
    ).fetchone()
    assert audit["mode"] == "auto"
    assert audit["actor"] == "auto"
    assert audit["effective_at"] == _TIME_TEXT
    assert audit["created_at"] == _TIME_TEXT
    assert audit["effective_at"].endswith("+08:00")
    assert audit["alias_row_table"] == "team_aliases"
    alias = conn.execute("SELECT alias, source, team_id FROM team_aliases WHERE alias = 'AC 米兰'").fetchone()
    assert alias["team_id"] == milan
    assert alias["source"] == "auto:loose_key_equal"
    repeated = alias_pool.pool_observe(conn, "team", "AC 米兰", "unit_test", "matches.id=8#home_team")
    assert repeated["repeated"] is True
    assert repeated["audit_id"] == level2["audit_id"]
    assert conn.execute("SELECT COUNT(*) FROM team_aliases WHERE alias = 'AC 米兰'").fetchone()[0] == 1
    assert _predictions(conn) == frozen


def test_loose_alias_of_another_team_does_not_auto_map(monkeypatch):
    """标准名宽松键相同，但另一支球队的别名也使用这个宽松键时，不自动映射。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _connect()
    _team(conn, "AC米兰")
    other = _team(conn, "国际米兰")
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('A.C.米兰', ?, 'seed')",
        (other,),
    )
    result = alias_pool.pool_observe(conn, "team", "AC-米兰", "unit_test", "conflict-alias")
    assert result["action"] == "pooled"
    assert result["status"] == "ambiguous"
    assert result["manual_reason"] == "multi_candidate"
    assert _audit_count(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM team_aliases WHERE source LIKE 'auto:%'").fetchone()[0] == 0


def test_alias_loose_key_without_same_canonical_loose_key_stays_hint(monkeypatch):
    """别名的宽松键相同，且该别名只指向一个标准名时，按等级 2 自动映射。

    标准名本身的宽松键可以不同。别名指向多个标准名，或赛事没有交集时，不在这个用例里自动映射。
    """
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _connect()
    team_id = _team(conn, "曼彻斯特联")
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('曼联', ?, 'seed')",
        (team_id,),
    )
    result = alias_pool.pool_observe(conn, "team", "曼 联", "unit_test", "alias-only-loose")
    assert result["action"] == "auto"
    assert result["criterion"] == "loose_key_equal"
    assert result["confidence"] == "level2_equal_after_cleaning"
    assert result["standard_id"] == team_id
    assert result["alias_inserted"] is True
    assert "曼彻斯特联" in result["message"]
    stored = conn.execute(
        "SELECT team_id, source FROM team_aliases WHERE alias = '曼 联'"
    ).fetchone()
    assert stored["team_id"] == team_id
    assert stored["source"] == "auto:loose_key_equal"
    assert _audit_count(conn) == 1


def test_prefix_difference_and_competition_mismatch_stay_in_pool(monkeypatch):
    """前后缀不同只给候选。来源赛事和球队已有赛事没有交集时也不自动映射。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _connect()
    salzburg = _team(conn, "萨尔茨堡")
    united = _team(conn, "曼联")
    conn.execute(
        "INSERT INTO matches (id, home_team, competition_name, home_team_id) VALUES (1, '曼联', '英格兰超级联赛', ?)",
        (united,),
    )
    conn.execute("INSERT INTO matches (id, home_team, competition_name) VALUES (2, '曼 联', '欧洲冠军联赛')")
    prefix = alias_pool.pool_observe(conn, "team", "FC 萨尔茨堡", "unit_test", "prefix")
    translated = alias_pool.pool_observe(conn, "team", "FC 萨尔斯堡", "unit_test", "translated-name")
    assert prefix["action"] == "pooled"
    assert prefix["status"] == "pending"
    assert prefix["manual_reason"] is None
    assert prefix["candidate_id"] == salzburg
    assert prefix["candidate_basis"] == "affix"
    assert translated["action"] == "pooled"
    assert translated["candidate_id"] is None
    mismatch = alias_pool.pool_observe(
        conn,
        "team",
        "曼 联",
        "unit_test",
        "matches.id=2#home_team",
        {"competition_name": "欧洲冠军联赛"},
    )
    assert mismatch["action"] == "pooled"
    assert mismatch["status"] == "pending"
    assert "赛事" in mismatch["candidate_reason"]
    assert mismatch["candidate_id"] == united
    assert conn.execute("SELECT home_team_id FROM matches WHERE id = 2").fetchone()[0] is None
    assert _audit_count(conn) == 0


def test_revoke_stops_later_automatic_mapping(monkeypatch):
    """撤销会删掉本次新写的别名、恢复身份编号，并让同一宽松键以后不再自动映射。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _connect()
    frozen = _predictions(conn)
    milan = _team(conn, "AC米兰")
    united = _team(conn, "曼联")
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('曼彻斯特联', ?, 'seed')",
        (united,),
    )
    conn.execute(
        "INSERT INTO matches (id, home_team, away_team, competition_name, away_team_id) VALUES (7, 'AC 米兰', '客队', '欧冠', 9)"
    )
    conn.execute("INSERT INTO matches (id, home_team, competition_name) VALUES (8, 'AC.米兰', '欧冠')")
    conn.execute("INSERT INTO matches (id, home_team) VALUES (11, '曼彻斯特联')")
    mapped = alias_pool.pool_observe(conn, "team", "AC 米兰", "unit_test", "matches.id=7#home_team")
    assert mapped["action"] == "auto"
    assert conn.execute("SELECT home_team_id, away_team_id FROM matches WHERE id = 7").fetchone()[:] == (milan, 9)
    level1 = alias_pool.pool_observe(conn, "team", "曼彻斯特联", "unit_test", "matches.id=11#home_team")
    assert level1["alias_inserted"] is False
    revoked = alias_pool.revoke_mapping(conn, mapped["audit_id"], "测试人员")
    assert revoked["action"] == "revoked"
    assert revoked["alias_deleted"] is True
    assert revoked["matches_restored"] == 1
    restored = conn.execute("SELECT home_team_id, away_team_id, league_id FROM matches WHERE id = 7").fetchone()
    assert restored["home_team_id"] is None
    assert restored["away_team_id"] == 9
    assert restored["league_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM team_aliases WHERE alias = 'AC 米兰'").fetchone()[0] == 0
    block = conn.execute("SELECT norm_key, reason, created_at FROM alias_auto_block").fetchone()
    assert block["norm_key"] == alias_pool.norm_key("AC 米兰")
    assert block["reason"] == "auto_revoked"
    assert block["created_at"] == _TIME_TEXT
    original = conn.execute(
        "SELECT revoked_by_audit_id FROM alias_mapping_audit WHERE id = ?",
        (mapped["audit_id"],),
    ).fetchone()
    assert original["revoked_by_audit_id"] == revoked["audit_id"]
    revoke_row = conn.execute(
        "SELECT mode, criterion, confidence, effective_at, actor FROM alias_mapping_audit WHERE id = ?",
        (revoked["audit_id"],),
    ).fetchone()
    assert revoke_row["mode"] == "revoke"
    assert revoke_row["criterion"] == "revoke"
    assert revoke_row["confidence"] == "revoke"
    assert revoke_row["effective_at"] == _TIME_TEXT
    assert revoke_row["actor"] == "测试人员"
    again = alias_pool.pool_observe(conn, "team", "AC 米兰", "unit_test", "matches.id=8#home_team")
    assert again["action"] == "pooled"
    assert again["status"] == "ambiguous"
    assert again["manual_reason"] == "auto_revoked"
    assert conn.execute("SELECT home_team_id FROM matches WHERE id = 8").fetchone()[0] is None
    dotted = alias_pool.pool_observe(conn, "team", "ＡＣ．米兰", "unit_test", "dotted-after-revoke")
    assert dotted["action"] == "pooled"
    assert dotted["manual_reason"] == "auto_revoked"
    assert conn.execute("SELECT COUNT(*) FROM team_aliases WHERE team_id = ?", (milan,)).fetchone()[0] == 0
    revoked_level1 = alias_pool.revoke_mapping(conn, level1["audit_id"], "测试人员")
    assert revoked_level1["alias_deleted"] is False
    assert conn.execute("SELECT COUNT(*) FROM team_aliases WHERE alias = '曼彻斯特联'").fetchone()[0] == 1
    blocked_known = alias_pool.pool_observe(conn, "team", "曼彻斯特联", "unit_test", "matches.id=12#home_team")
    assert blocked_known["action"] == "pooled"
    assert blocked_known["manual_reason"] == "auto_revoked"
    assert _predictions(conn) == frozen


def test_revoke_skips_identity_column_changed_after_mapping(monkeypatch):
    """映射写入的编号若随后被改过，撤销时不覆盖该列，并在撤销审计里写明跳过原因。

    映射没有改过的列也保持现状，即使映射之后被人改过，也不会被撤销改回去。
    """
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _connect()
    frozen = _predictions(conn)
    milan = _team(conn, "AC米兰")
    other = _team(conn, "国际米兰")
    conn.execute(
        "INSERT INTO matches (id, home_team, away_team, competition_name, away_team_id) VALUES (7, 'AC 米兰', '客队', '欧冠', 9)"
    )
    mapped = alias_pool.pool_observe(conn, "team", "AC 米兰", "unit_test", "matches.id=7#home_team")
    assert mapped["action"] == "auto"
    assert conn.execute("SELECT home_team_id FROM matches WHERE id = 7").fetchone()[0] == milan
    conn.execute("UPDATE matches SET home_team_id = ?, away_team_id = 10 WHERE id = 7", (other,))
    revoked = alias_pool.revoke_mapping(conn, mapped["audit_id"], "测试人员")
    assert revoked["matches_restored"] == 0
    assert revoked["alias_deleted"] is True
    row = conn.execute("SELECT home_team_id, away_team_id, league_id FROM matches WHERE id = 7").fetchone()
    assert row["home_team_id"] == other
    assert row["away_team_id"] == 10
    assert row["league_id"] is None
    audit = conn.execute(
        "SELECT mode, before_json, after_json FROM alias_mapping_audit WHERE id = ?",
        (revoked["audit_id"],),
    ).fetchone()
    assert audit["mode"] == "revoke"
    before_payload = json.loads(audit["before_json"])
    payload = json.loads(audit["after_json"])
    assert before_payload["matches"][0]["home_team_id"] == other
    assert before_payload["matches"][0]["away_team_id"] == 10
    assert payload["matches"][0]["home_team_id"] == other
    assert payload["matches"][0]["away_team_id"] == 10
    assert payload["skip_reason"]
    assert "主队编号" in payload["skip_reason"]
    assert "跳过" in payload["skip_reason"]
    assert "改过" in payload["skip_reason"]
    assert "客队编号" not in payload["skip_reason"]
    assert _audit_count(conn) == 2
    assert _predictions(conn) == frozen


def test_revoke_direct_auto_mapping_creates_pool_entry_and_sighting(monkeypatch):
    """直接自动映射、从未入池的名称，撤销后要有待定池记录和一条出现记录，不能只写禁止自动名单。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _connect()
    frozen = _predictions(conn)
    _team(conn, "AC米兰")
    mapped = alias_pool.pool_observe(conn, "team", "AC 米兰", "unit_test", "direct-auto")
    assert mapped["action"] == "auto"
    assert mapped["entry_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM alias_pool_entry").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM alias_pool_sighting").fetchone()[0] == 0
    revoked = alias_pool.revoke_mapping(conn, mapped["audit_id"], "测试人员")
    assert revoked["pool_created"] is True
    assert revoked["manual_reason"] == "auto_revoked"
    entry = conn.execute("SELECT * FROM alias_pool_entry").fetchone()
    assert entry["status"] == "ambiguous"
    assert entry["manual_reason"] == "auto_revoked"
    assert entry["seen_count"] == 1
    assert entry["norm_key"] == alias_pool.norm_key("AC 米兰")
    assert entry["first_source"] == "unit_test"
    assert entry["first_source_ref"] == "direct-auto"
    assert "撤销" in entry["candidate_reason"]
    assert "AC米兰" in entry["candidate_reason"]
    sighting = conn.execute("SELECT source, source_ref, context_json FROM alias_pool_sighting").fetchone()
    assert sighting["source"] == "unit_test"
    assert sighting["source_ref"] == "direct-auto"
    assert json.loads(sighting["context_json"]) == {}
    assert conn.execute("SELECT COUNT(*) FROM alias_pool_sighting").fetchone()[0] == 1
    block = conn.execute("SELECT reason FROM alias_auto_block").fetchone()
    assert block["reason"] == "auto_revoked"
    assert conn.execute("SELECT COUNT(*) FROM alias_pool_entry").fetchone()[0] == 1
    assert _predictions(conn) == frozen


def test_alias_loose_key_hit_auto_maps_when_alias_points_to_one_standard(monkeypatch):
    """宽松键命中唯一别名时自动映射。别名指向多个标准名，或赛事没有交集时，仍然进入待定池。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _connect()
    united = _team(conn, "曼彻斯特联")
    conn.execute(
        "INSERT INTO team_aliases (alias, team_id, source) VALUES ('曼联', ?, 'seed')",
        (united,),
    )
    conn.execute("INSERT INTO matches (id, home_team, competition_name) VALUES (21, '曼·联', '英超')")
    hit = alias_pool.pool_observe(conn, "team", "曼·联", "unit_test", "matches.id=21#home_team")
    assert hit["action"] == "auto"
    assert hit["criterion"] == "loose_key_equal"
    assert hit["confidence"] == "level2_equal_after_cleaning"
    assert hit["standard_id"] == united
    assert hit["alias_inserted"] is True
    assert hit["match_updated"] is True
    assert conn.execute("SELECT home_team_id FROM matches WHERE id = 21").fetchone()[0] == united

    gunners = _team(conn, "阿森纳")
    ajax = _team(conn, "阿贾克斯")
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('枪手', ?, 'seed')", (gunners,))
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('枪 手', ?, 'seed')", (ajax,))
    split = alias_pool.pool_observe(conn, "team", "枪·手", "unit_test", "alias-split")
    assert split["action"] == "pooled"
    assert split["status"] == "ambiguous"
    assert split["manual_reason"] == "multi_candidate"
    assert conn.execute("SELECT COUNT(*) FROM team_aliases WHERE alias = '枪·手'").fetchone()[0] == 0

    city = _team(conn, "曼彻斯特城")
    conn.execute("INSERT INTO team_aliases (alias, team_id, source) VALUES ('曼城', ?, 'seed')", (city,))
    conn.execute(
        "INSERT INTO matches (id, home_team, competition_name, home_team_id) VALUES (30, '曼城', '英格兰超级联赛', ?)",
        (city,),
    )
    conn.execute("INSERT INTO matches (id, home_team, competition_name) VALUES (22, '曼 城', '欧洲冠军联赛')")
    mismatch = alias_pool.pool_observe(
        conn,
        "team",
        "曼 城",
        "unit_test",
        "matches.id=22#home_team",
        {"competition_name": "欧洲冠军联赛"},
    )
    assert mismatch["action"] == "pooled"
    assert mismatch["status"] == "pending"
    assert mismatch["manual_reason"] is None
    assert mismatch["candidate_id"] == city
    assert "赛事" in mismatch["candidate_reason"]
    assert conn.execute("SELECT home_team_id FROM matches WHERE id = 22").fetchone()[0] is None


def test_formal_database_write_requires_backup_acknowledgement(monkeypatch, tmp_path):
    """写入入口先经过正式库保护。没有备份确认时拒绝写正式库，已归档副本即使已备份也拒绝。"""
    database = (tmp_path / "formal.db").resolve()
    conn = sqlite3.connect(str(database))
    try:
        monkeypatch.setattr(formal_db, "FORMAL_DB_PATH", database)
        monkeypatch.delenv(formal_db.BACKUP_ENV, raising=False)
        with pytest.raises(SystemExit) as refused:
            alias_pool.ensure_schema(conn)
        assert "备份" in str(refused.value)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'alias_pool_entry'"
            ).fetchone()[0]
            == 0
        )
        monkeypatch.setenv(formal_db.BACKUP_ENV, "1")
        alias_pool.ensure_schema(conn)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'alias_pool_entry'"
            ).fetchone()[0]
            == 1
        )
        monkeypatch.delenv(formal_db.BACKUP_ENV, raising=False)
        with pytest.raises(SystemExit) as observe_refused:
            alias_pool.pool_observe(conn, "team", "科特布斯", "unit_test", "formal-observe")
        assert "备份" in str(observe_refused.value)
        assert conn.execute("SELECT COUNT(*) FROM alias_pool_entry").fetchone()[0] == 0
        monkeypatch.setattr(formal_db, "ARCHIVED_V2D3_PATH", database)
        monkeypatch.setenv(formal_db.BACKUP_ENV, "1")
        with pytest.raises(SystemExit) as archived:
            alias_pool.revoke_mapping(conn, 1, "测试人员")
        assert "只读归档" in str(archived.value)
    finally:
        conn.close()


def test_competition_level2_updates_league_id_only(monkeypatch):
    """赛事名走同一套判据，只回填赛事编号。"""
    monkeypatch.setattr(alias_pool, "now_beijing_text", _Clock())
    conn = _connect()
    league_id = _league(conn, "欧联杯")
    conn.execute("INSERT INTO matches (id, competition_name, home_team_id) VALUES (3, '欧 联 杯', 4)")
    result = alias_pool.pool_observe(conn, "competition", "欧 联 杯", "unit_test", "matches.id=3#competition_name")
    assert result["action"] == "auto"
    assert result["standard_id"] == league_id
    row = conn.execute("SELECT home_team_id, away_team_id, league_id FROM matches WHERE id = 3").fetchone()
    assert row["league_id"] == league_id
    assert row["home_team_id"] == 4
    assert row["away_team_id"] is None
    women = alias_pool.pool_observe(conn, "competition", "女子世界杯", "unit_test", "women-cup")
    assert women["action"] == "pooled"
    assert women["manual_reason"] == "gender"


def test_schema_can_be_applied_repeatedly_without_dropping_rows():
    """建表语句重复执行时保留已有的行，并且不会加入自动迁移。"""
    conn = _connect()
    alias_pool.pool_observe(conn, "team", "科特布斯", "unit_test", "seed-row")
    alias_pool.ensure_schema(conn)
    alias_pool.ensure_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM alias_pool_entry").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM alias_pool_sighting").fetchone()[0] == 1
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'alias_%'"
        )
    }
    assert names == {
        "alias_pool_entry",
        "alias_pool_sighting",
        "alias_mapping_audit",
        "alias_auto_block",
    }
    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_alias_%'"
        )
    }
    assert "idx_alias_pool_entry_status" in indexes
    assert "idx_alias_pool_entry_loose" in indexes
    assert "idx_alias_audit_raw" in indexes
    assert "idx_alias_audit_batch" in indexes
    script = alias_pool.schema_path().read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS alias_pool_entry" in script
    assert "CREATE TABLE IF NOT EXISTS alias_pool_sighting" in script
    assert "CREATE TABLE IF NOT EXISTS alias_mapping_audit" in script
    assert "CREATE TABLE IF NOT EXISTS alias_auto_block" in script
    assert "effective_at" in script
    assert "ALTER TABLE" not in script
    assert "DROP TABLE" not in script
    assert alias_pool.schema_path().name not in {path.name for path in MIGRATION_PATHS}
    source = Path(alias_pool.__file__).read_text(encoding="utf-8")
    assert "fcntl" not in source
    assert "msvcrt" not in source
    assert "sqlite3.connect" not in source
    assert "data/app.db" not in source
    assert "guard_write_target" in source
    assert "FORMAL_DB_BACKUP_DONE" in source


def test_empty_name_is_skipped_and_beijing_time_uses_offset():
    """空名称不写入。真实时钟生成的时间带有东八区偏移。"""
    conn = _connect()
    skipped = alias_pool.pool_observe(conn, "team", "   ", "unit_test", "blank")
    assert skipped["action"] == "skipped"
    assert conn.execute("SELECT COUNT(*) FROM alias_pool_entry").fetchone()[0] == 0
    moment = alias_pool.now_beijing_text()
    assert moment.endswith("+08:00")
    assert "T" in moment
    try:
        alias_pool.pool_observe(conn, "club", "曼联", "unit_test", "bad-kind")
    except ValueError as exc:
        assert "team" in str(exc)
    else:
        raise AssertionError("非法类别应当被拒绝。")
