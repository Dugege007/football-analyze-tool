"""竞彩列表消失、推迟后再出现。不写现网库，不调用 5DF。"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("FAT_DISABLE_DOTENV", "1")

import live_capture as lc

TZ = lc.TZ


def _csl(items):
    return {"fetched_at": lc.iso(lc.now_cn()), "data": [
        {"id": fid, "league": {"name": "挪超"}, "teams": {"home": {"name": h}, "away": {"name": a}},
         "kickoff_ts": int(k.timestamp()), "status": st,
         "lottery": {"jingcailottery": {"number": num, "odds": None}}}
        for fid, num, h, a, k, st in items
    ]}


def _env(tmp_path, monkeypatch):
    for name in ("D_ALL", "D_PIN", "D_CSL", "D_PLAN", "D_STAGE", "D_STATE", "D_LOG", "D_SUM", "D_BAK"):
        monkeypatch.setattr(lc, name, tmp_path / name.lower())
    monkeypatch.setattr(lc, "LIVE", tmp_path)


def test_drop_stays_in_number_order_as_other(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    D = "2026-11-07"
    day = datetime(2026, 11, 7, tzinfo=TZ)
    full = [
        (1, "周六001", "甲", "乙", day.replace(hour=18), "scheduled"),
        (18, "周六018", "克里斯达桑德BK", "瓦勒伦加", day.replace(hour=23), "scheduled"),
        (19, "周六019", "丙", "丁", day.replace(hour=23, minute=30), "scheduled"),
    ]
    lc.build_plan(D, _csl(full), now=day.replace(hour=2))
    plan = lc.build_plan(D, _csl([full[0], full[2]]), now=day.replace(hour=2, minute=51))
    codes = [m["jc_code"] for m in plan["matches"]]
    assert codes == ["六001", "六018", "六019"]
    dropped = next(m for m in plan["matches"] if m["jc_code"] == "六018")
    assert dropped["schedule_status"] == "other"
    assert dropped["schedule_status_evidence"] == "dropped_from_csl"
    assert dropped["absent_from_csl"] is True
    assert not any(t["match_uid"] == "2026-11-07|六018" for t in plan["targets"])
    assert any(e.get("event") == "dropped_from_csl" and e.get("match_uid") == "2026-11-07|六018"
               for e in plan["kickoff_log"])
    # 再刷新一次，仍然缺席，不把其他改成取消。
    again = lc.build_plan(D, _csl([full[0], full[2]]), now=day.replace(hour=3))
    again_row = next(m for m in again["matches"] if m["jc_code"] == "六018")
    assert again_row["schedule_status"] == "other"


def test_reappear_with_new_kickoff_clears_the_drop_and_records_postpone(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    D = "2026-11-07"
    day = datetime(2026, 11, 7, tzinfo=TZ)
    original = day.replace(hour=23)
    later = datetime(2026, 11, 9, 19, tzinfo=TZ)
    first = [(18, "周六018", "克里斯达桑德BK", "瓦勒伦加", original, "scheduled")]
    lc.build_plan(D, _csl(first), now=day.replace(hour=1))
    lc.build_plan(D, _csl([]), now=day.replace(hour=2, minute=51))
    back = lc.build_plan(D, _csl([
        (18, "周六018", "克里斯达桑德BK", "瓦勒伦加", later, "scheduled"),
    ]), now=datetime(2026, 11, 8, 10, tzinfo=TZ))
    row = back["matches"][0]
    assert row["absent_from_csl"] is False
    assert row["schedule_status"] == "postponed"
    assert row["schedule_status_evidence"] == "kickoff_rescheduled"
    assert row["kickoff_rev"] == 1
    assert row["kickoff_actual"][11:16] == "19:00"
    assert row["kickoff_original"][11:16] == "23:00"
    assert row["postponed_announced_at"] is None


def test_fifteen_minute_change_does_not_become_postpone(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    D = "2026-11-07"
    day = datetime(2026, 11, 7, tzinfo=TZ)
    lc.build_plan(D, _csl([
        (1, "周六001", "甲", "乙", day.replace(hour=15, minute=30), "scheduled"),
    ]), now=day.replace(hour=9))
    plan = lc.build_plan(D, _csl([
        (1, "周六001", "甲", "乙", day.replace(hour=15, minute=45), "scheduled"),
    ]), now=day.replace(hour=10))
    row = plan["matches"][0]
    assert row["kickoff_rev"] == 1
    assert row["schedule_status"] is None


def test_source_cancelled_is_cancelled(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    D = "2026-11-07"
    day = datetime(2026, 11, 7, tzinfo=TZ)
    plan = lc.build_plan(D, _csl([
        (2, "周六002", "甲", "乙", day.replace(hour=19), "cancelled"),
    ]), now=day.replace(hour=12))
    row = plan["matches"][0]
    assert row["schedule_status"] == "cancelled"
    assert row["schedule_status_evidence"] == "5df_status_cancelled"
    assert row["absent_from_csl"] is False


def test_persist_writes_confirmed_postpone_without_moving_the_jc_hour(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    D = "2026-11-07"
    day = datetime(2026, 11, 7, tzinfo=TZ)
    later = datetime(2026, 11, 9, 19, tzinfo=TZ)
    lc.build_plan(D, _csl([
        (18, "周六018", "克里斯达桑德BK", "瓦勒伦加", day.replace(hour=23), "scheduled"),
    ]), now=day.replace(hour=1))
    lc.build_plan(D, _csl([]), now=day.replace(hour=2, minute=51))
    db = tmp_path / "v2d3" / "app.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE matches (id INTEGER PRIMARY KEY, match_uid TEXT, kickoff_at TEXT, kickoff_hour INTEGER)"
    )
    conn.execute(
        "INSERT INTO matches (match_uid, kickoff_at, kickoff_hour) VALUES (?,?,?)",
        ("2026-11-07|六018", "2026-11-07T23:00:00+08:00", 23),
    )
    conn.commit()
    conn.close()
    lc.persist_schedule_status(D, db, lc.load_plan(D))
    lc.build_plan(D, _csl([
        (18, "周六018", "克里斯达桑德BK", "瓦勒伦加", later, "scheduled"),
    ]), now=datetime(2026, 11, 8, 10, tzinfo=TZ))
    lc.persist_schedule_status(D, db, lc.load_plan(D))
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM matches").fetchone()
    conn.close()
    assert row["schedule_status"] == "postponed"
    assert row["schedule_status_evidence"] == "kickoff_rescheduled"
    assert row["kickoff_at"].startswith("2026-11-09T19:00:00")
    assert row["kickoff_actual"].startswith("2026-11-09T19:00:00")
    assert row["kickoff_original"].startswith("2026-11-07T23:00:00")
    assert row["kickoff_hour"] == 23
    assert row["postponed_announced_at"] is None
    assert row["postpone_ts_unknown"] == 1
    assert row["kickoff_rev"] == 1


def test_persist_writes_replica_only(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    D = "2026-11-07"
    day = datetime(2026, 11, 7, tzinfo=TZ)
    plan = lc.build_plan(D, _csl([
        (18, "周六018", "克里斯达桑德BK", "瓦勒伦加", day.replace(hour=23), "scheduled"),
    ]), now=day.replace(hour=1))
    lc.build_plan(D, _csl([]), now=day.replace(hour=2, minute=51))
    db = tmp_path / "v2d3" / "app.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE matches (id INTEGER PRIMARY KEY, match_uid TEXT, kickoff_at TEXT, kickoff_hour INTEGER)"
    )
    conn.execute(
        "INSERT INTO matches (match_uid, kickoff_at, kickoff_hour) VALUES (?,?,?)",
        ("2026-11-07|六018", "2026-11-07T23:00:00+08:00", 23),
    )
    conn.commit()
    conn.close()
    saved = lc.load_plan(D)
    report = lc.persist_schedule_status(D, db, saved)
    assert report["matches_updated"] == 1
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM matches").fetchone()
    assert row["schedule_status"] == "other"
    assert row["schedule_status_evidence"] == "dropped_from_csl"
    assert row["kickoff_hour"] == 23
    conn.close()
    # 正式库路径拒绝写入。
    prod = tmp_path / "prod" / "app.db"
    prod.parent.mkdir()
    prod.write_bytes(db.read_bytes())
    monkeypatch.setattr(lc, "PROD_DB", prod.resolve())
    monkeypatch.setattr(lc, "_FORMAL_MERGED", False)
    try:
        lc.persist_schedule_status(D, prod, plan)
        raised = False
    except SystemExit:
        raised = True
    assert raised
