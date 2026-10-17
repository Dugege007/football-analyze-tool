# -*- coding: utf-8 -*-
"""bsd_event_map 对照与 dc_train 中英别名导入。匹配逻辑用假事件源，不访问 BSD。"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bsd_event_map_build as m  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "bsd_event_map_build.py"


def _cli_env() -> dict[str, str]:
    """子进程沿用当前环境，并强制 UTF-8 模式。

    Windows 中文 locale 下，未指定 encoding 时 subprocess 按 GBK 解码标准输出，
    无法正确读回帮助文本和导入摘要中的中文。PYTHONUTF8=1 让子进程以 UTF-8 写出。
    """
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    return env


SCHEMA = """
CREATE TABLE teams (
  id INTEGER PRIMARY KEY,
  name_zh_canonical TEXT NOT NULL UNIQUE,
  note TEXT
);
CREATE TABLE team_aliases (
  id INTEGER PRIMARY KEY,
  alias TEXT NOT NULL UNIQUE,
  team_id INTEGER NOT NULL,
  source TEXT,
  created_at TEXT
);
CREATE TABLE matches (
  id INTEGER PRIMARY KEY,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  kickoff_at TEXT,
  kickoff_actual TEXT,
  home_team_id INTEGER,
  away_team_id INTEGER
);
CREATE TABLE results (
  match_id INTEGER PRIMARY KEY,
  home_goals INTEGER,
  away_goals INTEGER
);
"""


def make_db(tmp_path: Path) -> Path:
    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO teams(id, name_zh_canonical) VALUES (?,?)",
        [(1, "阿森纳"), (2, "曼城"), (3, "切尔西")],
    )
    conn.executemany(
        "INSERT INTO team_aliases(alias, team_id, source) VALUES (?,?,?)",
        [("阿森纳", 1, "seed"), ("曼城", 2, "seed"), ("切尔西", 3, "seed")],
    )
    conn.execute(
        "INSERT INTO matches(id, home_team, away_team, kickoff_at, home_team_id, away_team_id) "
        "VALUES (1,'阿森纳','曼城','2026-08-02T19:30:00+08:00',1,2)"
    )
    conn.execute("INSERT INTO results(match_id, home_goals, away_goals) VALUES (1,2,1)")
    conn.commit()
    conn.close()
    return db


def add_match(db: Path, mid: int, home: str, away: str, kickoff: str, hid: int, aid: int, score=(2, 1), actual=None):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO matches(id, home_team, away_team, kickoff_at, kickoff_actual, home_team_id, away_team_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (mid, home, away, kickoff, actual, hid, aid),
    )
    if score is not None:
        conn.execute(
            "INSERT INTO results(match_id, home_goals, away_goals) VALUES (?,?,?)",
            (mid, score[0], score[1]),
        )
    conn.commit()
    conn.close()


def english_aliases(db: Path):
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO team_aliases(alias, team_id, source) VALUES (?,?,?)",
        [("Arsenal", 1, "test"), ("Manchester City", 2, "test"), ("Chelsea", 3, "test")],
    )
    conn.commit()
    conn.close()


def event(eid, home, away, when, hs=2, as_=1, home_id=None, away_id=None):
    return {
        "id": eid,
        "home_team": home,
        "away_team": away,
        "event_date": when,
        "home_score": hs,
        "away_score": as_,
        "home_team_id": home_id,
        "away_team_id": away_id,
    }


class FakeEvents:
    def __init__(self, by_date):
        self.by_date = by_date
        self.calls = []

    def get_events(self, date_from, date_to):
        self.calls.append((date_from, date_to))
        assert date_from == date_to
        return list(self.by_date.get(date_from, [])), 1


def ns(db, out, **kw):
    data = dict(
        db=str(db),
        apply=False,
        import_aliases_from_dc_train=False,
        aliases_only=False,
        aliases_csv=None,
        min_confidence="med",
        final_names=None,
        no_final_names=True,
        events_json=None,
        manual_csv=None,
        out_dir=str(out),
        limit=None,
        quiet=True,
    )
    data.update(kw)
    return argparse.Namespace(**data)


def table_names(db: Path) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def map_rows(db: Path):
    conn = sqlite3.connect(db)
    try:
        if "bsd_event_map" not in table_names(db):
            return []
        return conn.execute("SELECT match_id, event_id, note FROM bsd_event_map ORDER BY match_id").fetchall()
    finally:
        conn.close()


def test_help_lists_dry_run_apply_and_alias_import():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_cli_env(),
        check=True,
    )
    text = proc.stdout
    assert "--apply" in text
    assert "--import-aliases-from-dc-train" in text
    assert "kickoff15_alias" in text or "15" in text
    assert "no_candidate" in text
    assert "BSD_BZZOIRO_API_KEY" in text
    assert "不写库" in text or "干跑" in text


def test_unique_match_dry_run_does_not_write_apply_does(tmp_path):
    db = make_db(tmp_path)
    english_aliases(db)
    out = tmp_path / "out"
    src = FakeEvents({
        "2026-08-02": [event(900, "Arsenal", "Manchester City", "2026-08-02T11:30:00+00:00")],
    })
    dry = m.run(ns(db, out), client=src)
    assert dry["mappable"] == 1 and dry["wrote_rows"] == 0
    assert dry["unmatched"] == {}
    assert "bsd_event_map" not in table_names(db)
    mapped = list(csv.DictReader((out / "event_map.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert mapped[0]["event_id"] == "900" and mapped[0]["note"] == "kickoff15_alias"
    applied = m.run(ns(db, out, apply=True), client=src)
    assert applied["wrote_rows"] == 1
    assert map_rows(db) == [(1, 900, "kickoff15_alias")]
    again = m.run(ns(db, out, apply=True), client=src)
    assert again["wrote_rows"] == 0 and again["skipped_existing"] == 1
    assert map_rows(db) == [(1, 900, "kickoff15_alias")]


def test_fifteen_minutes_inclusive_and_swapped_and_near_name(tmp_path):
    db = make_db(tmp_path)
    english_aliases(db)
    out = tmp_path / "out"
    # 19:45+08 = 11:45Z，与 19:30 正好差 15 分钟。
    ok = m.run(ns(db, out), client=FakeEvents({
        "2026-08-02": [event(1, "arsenal", "Manchester City", "2026-08-02T11:45:00+00:00")],
    }))
    assert ok["mappable"] == 1
    over = m.run(ns(db, out), client=FakeEvents({
        "2026-08-02": [event(2, "Arsenal", "Manchester City", "2026-08-02T11:45:01+00:00")],
    }))
    assert over["mappable"] == 0 and over["unmatched"] == {"no_candidate": 1}
    swapped = m.run(ns(db, out), client=FakeEvents({
        "2026-08-02": [event(3, "Manchester City", "Arsenal", "2026-08-02T11:30:00+00:00")],
    }))
    assert swapped["unmatched"] == {"no_candidate": 1}
    fuzzy = m.run(ns(db, out), client=FakeEvents({
        "2026-08-02": [event(4, "Arsenal FC", "Manchester City", "2026-08-02T11:30:00+00:00")],
    }))
    assert fuzzy["unmatched"] == {"no_candidate": 1}
    near = m.run(ns(db, out), client=FakeEvents({
        "2026-08-02": [event(5, "Arsenall", "Manchester City", "2026-08-02T11:30:00+00:00")],
    }))
    assert near["unmatched"] == {"no_candidate": 1}


def test_multi_candidate_does_not_pick_by_score(tmp_path):
    db = make_db(tmp_path)
    english_aliases(db)
    out = tmp_path / "out"
    src = FakeEvents({
        "2026-08-02": [
            event(11, "Arsenal", "Manchester City", "2026-08-02T11:30:00+00:00", hs=2, as_=1),
            event(12, "Arsenal", "Manchester City", "2026-08-02T11:36:00+00:00", hs=0, as_=0),
        ],
    })
    report = m.run(ns(db, out), client=src)
    assert report["mappable"] == 0
    assert report["unmatched"] == {"multi_candidate": 1}
    assert map_rows(db) == []


def test_score_mismatch_and_missing_score(tmp_path):
    db = make_db(tmp_path)
    english_aliases(db)
    out = tmp_path / "out"
    bad = m.run(ns(db, out), client=FakeEvents({
        "2026-08-02": [event(21, "Arsenal", "Manchester City", "2026-08-02T11:30:00+00:00", hs=1, as_=0)],
    }))
    assert bad["unmatched"] == {"score_mismatch": 1} and bad["mappable"] == 0
    # 同一天已有候选时不因比分不符再去邻日找另一场。
    assert bad["bsd_dates"] == ["2026-08-02"]
    missing = m.run(ns(db, out), client=FakeEvents({
        "2026-08-02": [event(22, "Arsenal", "Manchester City", "2026-08-02T11:30:00+00:00", hs=None, as_=None)],
    }))
    assert missing["mappable"] == 1
    assert missing["writes"][0]["score_checked"] is False


def test_team_unresolved_does_not_call_bsd(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE matches SET home_team='未知主', away_team='未知客', home_team_id=NULL, away_team_id=NULL WHERE id=1"
    )
    conn.commit()
    conn.close()
    out = tmp_path / "out"
    src = FakeEvents({})
    report = m.run(ns(db, out), client=src)
    assert report["unmatched"] == {"team_unresolved": 1}
    assert src.calls == []
    assert report["bsd_pages"] == 0


def test_expand_only_when_same_day_has_no_candidate(tmp_path):
    db = make_db(tmp_path)
    english_aliases(db)
    out = tmp_path / "out"
    # 事件在北京时间前一日的目录里，但时刻仍在 15 分钟内：当天不足才扩。
    late = event(31, "Arsenal", "Manchester City", "2026-08-01T16:20:00+00:00")
    # 19:30+08 = 11:30Z。改开赛到 00:10+08 = 前一日 16:10Z，差 10 分钟。
    conn = sqlite3.connect(db)
    conn.execute("UPDATE matches SET kickoff_at='2026-08-02T00:10:00+08:00' WHERE id=1")
    conn.commit()
    conn.close()
    src = FakeEvents({"2026-08-01": [late], "2026-08-02": [], "2026-08-03": []})
    report = m.run(ns(db, out), client=src)
    assert report["mappable"] == 1 and report["writes"][0]["event_id"] == 31
    assert "2026-08-02" in report["bsd_dates"] and "2026-08-01" in report["bsd_dates"]

    # 当天已经有一场候选时，不再为了邻日多拉。
    src2 = FakeEvents({
        "2026-08-02": [event(32, "Arsenal", "Manchester City", "2026-08-02T11:30:00+00:00")],
        "2026-08-01": [event(33, "Arsenal", "Manchester City", "2026-08-02T11:20:00+00:00")],
    })
    conn = sqlite3.connect(db)
    conn.execute("UPDATE matches SET kickoff_at='2026-08-02T19:30:00+08:00' WHERE id=1")
    conn.commit()
    conn.close()
    held = m.run(ns(db, out), client=src2)
    assert held["mappable"] == 1 and held["writes"][0]["event_id"] == 32
    assert src2.calls == [("2026-08-02", "2026-08-02")]


def test_midnight_neighbor_can_turn_unique_into_multi(tmp_path):
    db = make_db(tmp_path)
    english_aliases(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE matches SET kickoff_at='2026-08-02T00:10:00+08:00' WHERE id=1")
    conn.commit()
    conn.close()
    out = tmp_path / "out"
    src = FakeEvents({
        "2026-08-02": [event(41, "Arsenal", "Manchester City", "2026-08-01T16:10:00+00:00")],
        "2026-08-01": [event(42, "Arsenal", "Manchester City", "2026-08-01T16:00:00+00:00")],
    })
    report = m.run(ns(db, out), client=src)
    assert report["unmatched"] == {"multi_candidate": 1}
    assert ("2026-08-01", "2026-08-01") in src.calls


def test_same_event_claimed_by_two_matches_writes_neither(tmp_path):
    db = make_db(tmp_path)
    english_aliases(db)
    add_match(db, 2, "阿森纳", "曼城", "2026-08-02T19:32:00+08:00", 1, 2, score=(2, 1))
    out = tmp_path / "out"
    src = FakeEvents({
        "2026-08-02": [event(50, "Arsenal", "Manchester City", "2026-08-02T11:30:00+00:00")],
    })
    report = m.run(ns(db, out, apply=True), client=src)
    assert report["mappable"] == 0
    assert report["unmatched"] == {"event_id_conflict": 2}
    assert map_rows(db) == []


def test_kickoff_actual_preferred(tmp_path):
    db = make_db(tmp_path)
    english_aliases(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE matches SET kickoff_at='2026-08-01T19:30:00+08:00', kickoff_actual='2026-08-02T19:30:00+08:00' WHERE id=1"
    )
    conn.commit()
    conn.close()
    out = tmp_path / "out"
    src = FakeEvents({
        "2026-08-02": [event(60, "Arsenal", "Manchester City", "2026-08-02T11:30:00+00:00")],
        "2026-08-01": [],
    })
    report = m.run(ns(db, out), client=src)
    assert report["mappable"] == 1
    assert report["writes"][0]["kickoff_field"] == "kickoff_actual"


def test_alias_import_high_med_coverage_and_low_skipped(tmp_path):
    db = make_db(tmp_path)
    out = tmp_path / "out"
    csv_path = tmp_path / "aliases.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["alias", "team_canonical", "team_id", "confidence", "model_source_name", "bsd_team_id"],
        )
        writer.writeheader()
        writer.writerows([
            {"alias": "阿森纳", "team_canonical": "阿森纳", "team_id": "1", "confidence": "high",
             "model_source_name": "Arsenal", "bsd_team_id": "101"},
            {"alias": "曼城", "team_canonical": "曼城", "team_id": "2", "confidence": "med",
             "model_source_name": "Manchester City", "bsd_team_id": ""},
            {"alias": "切尔西", "team_canonical": "切尔西", "team_id": "3", "confidence": "low",
             "model_source_name": "Chelsea", "bsd_team_id": "103"},
        ])
    src = FakeEvents({
        "2026-08-02": [event(70, "Arsenal", "Manchester City", "2026-08-02T11:30:00+00:00")],
    })
    dry = m.run(ns(
        db, out, import_aliases_from_dc_train=True, aliases_csv=str(csv_path),
    ), client=src)
    assert dry["wrote_rows"] == 0
    assert dry["alias_import"]["coverage_before"]["both_sides_latin_alias"] == 0
    assert dry["alias_import"]["coverage_after"]["both_sides_latin_alias"] == 1
    assert dry["alias_import"]["map_before"]["mappable"] == 0
    assert dry["alias_import"]["map_after"]["mappable"] == 1
    assert dry["mappable"] == 1
    assert dry["alias_import"]["stats"]["skipped_low"] == 1
    assert dry["alias_import"]["stats"]["planned_insert"] == 2
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM team_aliases").fetchone()[0] == 3
    conn.close()
    # 同一天在前后两次对照里只拉一次。
    assert src.calls == [("2026-08-02", "2026-08-02")]

    applied = m.run(ns(
        db, out, apply=True, import_aliases_from_dc_train=True, aliases_csv=str(csv_path),
    ), client=src)
    assert applied["alias_import"]["written"] == 2
    assert applied["wrote_rows"] == 1
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT alias, team_id, source FROM team_aliases WHERE alias IN ('Arsenal','Manchester City','Chelsea')"
    ).fetchall()
    conn.close()
    got = {r[0]: (r[1], r[2]) for r in rows}
    assert "Chelsea" not in got
    assert got["Arsenal"][0] == 1 and "bsd_team_id=101" in got["Arsenal"][1] and "conf=high" in got["Arsenal"][1]
    assert got["Manchester City"][0] == 2 and "conf=med" in got["Manchester City"][1]
    # 再 apply 不重复插入、不覆盖映射。
    second = m.run(ns(
        db, out, apply=True, import_aliases_from_dc_train=True, aliases_csv=str(csv_path),
    ), client=src)
    assert second["alias_import"]["written"] == 0
    assert second["wrote_rows"] == 0
    assert map_rows(db) == [(1, 70, "kickoff15_alias")]


def test_min_confidence_high_skips_med(tmp_path):
    db = make_db(tmp_path)
    out = tmp_path / "out"
    path = tmp_path / "aliases.csv"
    path.write_text(
        "alias,team_canonical,team_id,confidence,model_source_name\n"
        "曼城,曼城,2,med,Manchester City\n",
        encoding="utf-8",
    )
    report = m.run(ns(
        db, out, aliases_only=True, import_aliases_from_dc_train=True,
        aliases_csv=str(path), min_confidence="high", apply=True,
    ))
    assert report["alias_import"]["stats"]["planned_insert"] == 0
    assert report["alias_import"]["stats"]["skipped_confidence"] == 1
    assert report["alias_import"]["written"] == 0


def test_same_english_name_two_teams_inserts_neither(tmp_path):
    db = make_db(tmp_path)
    out = tmp_path / "out"
    path = tmp_path / "aliases.csv"
    path.write_text(
        "alias,team_canonical,team_id,confidence,model_source_name\n"
        "阿森纳,阿森纳,1,high,Arsenal\n"
        "曼城,曼城,2,high,Arsenal\n",
        encoding="utf-8",
    )
    report = m.run(ns(
        db, out, apply=True, aliases_only=True, import_aliases_from_dc_train=True, aliases_csv=str(path),
    ))
    assert report["alias_import"]["stats"]["conflicts"] == 1
    assert report["alias_import"]["stats"]["planned_insert"] == 0
    assert report["alias_import"]["written"] == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM team_aliases WHERE alias='Arsenal'").fetchone()[0] == 0
    conn.close()


def test_alias_conflict_is_not_overwritten(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO team_aliases(alias, team_id, source) VALUES ('Arsenal',3,'seed')")
    conn.commit()
    conn.close()
    out = tmp_path / "out"
    path = tmp_path / "aliases.csv"
    path.write_text(
        "alias,team_canonical,team_id,confidence,model_source_name\n"
        "阿森纳,阿森纳,1,high,Arsenal\n",
        encoding="utf-8",
    )
    report = m.run(ns(
        db, out, apply=True, aliases_only=True, import_aliases_from_dc_train=True, aliases_csv=str(path),
    ))
    assert report["alias_import"]["stats"]["conflicts"] == 1
    assert report["alias_import"]["written"] == 0
    conn = sqlite3.connect(db)
    owner = conn.execute("SELECT team_id FROM team_aliases WHERE alias='Arsenal'").fetchone()[0]
    conn.close()
    assert owner == 3


def test_bsd_team_id_exact_and_disagreement(tmp_path):
    db = make_db(tmp_path)
    out = tmp_path / "out"
    path = tmp_path / "aliases.csv"
    path.write_text(
        "alias,team_canonical,team_id,confidence,model_source_name,bsd_team_id\n"
        "阿森纳,阿森纳,1,high,Arsenal,101\n"
        "曼城,曼城,2,high,Manchester City,202\n",
        encoding="utf-8",
    )
    # 队名对不上，只靠研究表里的 BSD team_id。
    by_id = m.run(ns(
        db, out, import_aliases_from_dc_train=True, aliases_csv=str(path),
    ), client=FakeEvents({
        "2026-08-02": [event(80, "Unknown Home", "Unknown Away", "2026-08-02T11:30:00+00:00",
                            home_id=101, away_id=202)],
    }))
    assert by_id["mappable"] == 1
    # 名字指向阿森纳，编号却登记在曼城：不猜。
    disagree = m.run(ns(
        db, out, import_aliases_from_dc_train=True, aliases_csv=str(path),
    ), client=FakeEvents({
        "2026-08-02": [event(81, "Arsenal", "Manchester City", "2026-08-02T11:30:00+00:00",
                            home_id=202, away_id=101)],
    }))
    assert disagree["mappable"] == 0
    assert disagree["unmatched"] == {"no_candidate": 1}


def test_manual_note_does_not_overwrite(tmp_path):
    db = make_db(tmp_path)
    out = tmp_path / "out"
    manual = tmp_path / "manual.csv"
    manual.write_text("match_id,event_id\n1,777\n", encoding="utf-8")
    report = m.run(ns(db, out, apply=True, manual_csv=str(manual)), client=FakeEvents({}))
    assert map_rows(db) == [(1, 777, "manual")]
    assert report["wrote_rows"] == 1
    manual.write_text("match_id,event_id\n1,778\n", encoding="utf-8")
    again = m.run(ns(db, out, apply=True, manual_csv=str(manual)), client=FakeEvents({}))
    assert again["wrote_rows"] == 0
    assert map_rows(db) == [(1, 777, "manual")]


def test_pagination_counts_pages():
    client = m.BsdClient("test-key")
    pages = {
        0: {"next": "https://sports.bzzoiro.com/api/v2/events/?offset=200", "results": [{"id": i} for i in range(200)]},
        200: {"next": None, "results": [{"id": 1000}]},
    }
    client._request = lambda params: pages[params["offset"]]
    events, page_count = client.get_events("2026-08-02", "2026-08-02")
    assert page_count == 2
    assert [e["id"] for e in events] == list(range(200)) + [1000]


def test_apply_formal_db_requires_backup(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    english_aliases(db)
    monkeypatch.setenv("APP_DB_PATH", str(db))
    monkeypatch.delenv("FORMAL_DB_BACKUP_DONE", raising=False)
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="REFUSE"):
        m.run(ns(db, out, apply=True), client=FakeEvents({
            "2026-08-02": [event(1, "Arsenal", "Manchester City", "2026-08-02T11:30:00+00:00")],
        }))
    assert map_rows(db) == []
    monkeypatch.setenv("FORMAL_DB_BACKUP_DONE", "1")
    report = m.run(ns(db, out, apply=True), client=FakeEvents({
        "2026-08-02": [event(1, "Arsenal", "Manchester City", "2026-08-02T11:30:00+00:00")],
    }))
    assert report["wrote_rows"] == 1


def test_import_aliases_subcommand(tmp_path):
    db = make_db(tmp_path)
    path = tmp_path / "aliases.csv"
    path.write_text(
        "alias,team_canonical,team_id,confidence,model_source_name\n"
        "阿森纳,阿森纳,1,high,Arsenal\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "import-aliases", "--db", str(db), "--aliases-csv", str(path),
         "--no-final-names", "--apply"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_cli_env(),
    )
    assert proc.returncode == 0, proc.stderr
    assert "拟插入 1" in proc.stdout
    assert "实际写入 1" in proc.stdout
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT team_id FROM team_aliases WHERE alias='Arsenal'").fetchone()
    conn.close()
    assert row == (1,)
    assert "bsd_event_map" not in table_names(db)


def test_sqlite_research_table(tmp_path):
    db = make_db(tmp_path)
    research = tmp_path / "dc_train.sqlite"
    conn = sqlite3.connect(research)
    conn.execute(
        "CREATE TABLE team_aliases_proposed (alias TEXT, team_canonical TEXT, team_id TEXT, "
        "confidence TEXT, model_source_name TEXT)"
    )
    conn.execute(
        "INSERT INTO team_aliases_proposed VALUES ('阿森纳','阿森纳','1','high','Arsenal')"
    )
    conn.commit()
    conn.close()
    out = tmp_path / "out"
    report = m.run(ns(
        db, out, aliases_only=True, import_aliases_from_dc_train=True,
        aliases_csv=str(research), apply=True,
    ))
    assert report["alias_import"]["written"] == 1
