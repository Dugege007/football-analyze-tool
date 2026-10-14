"""赛果定时入库的单元测试（只用临时库，不碰任何真实库）。"""
import datetime as dt
import fcntl
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import result_ingest as R  # noqa: E402

TZ = R.TZ


def mkdb(tmp: Path) -> str:
    d = tmp / "v2d3"
    d.mkdir()
    db = str(d / "app.db")
    c = sqlite3.connect(db)
    c.executescript("""
    create table import_batches(id integer primary key autoincrement, source_file text not null, month text,
      imported_at text not null default (datetime('now')), row_count integer, status text not null default 'ok');
    create table matches(id integer primary key, match_uid text, scope text, jingcai_date text, jc_id text,
      home_team text, away_team text, kickoff_at text);
    create table results(match_id integer primary key, home_goals integer, away_goals integer, total_goals integer, wdl text);
    insert into matches values (1,'a','jingcai','2026-10-10','六001','大阪樱花','横滨水手','2026-10-10T13:00:00+08:00');
    insert into matches values (2,'b','jingcai','2026-10-10','六002','首尔FC','济州SK','2026-10-10T14:00:00+08:00');
    insert into results values (2, 9, 9, 18, '平');
    """)
    c.commit()
    c.close()
    return db


def sp(code, date, h, a, score="1:0", pool="Payout"):
    return {"matchNumStr": code, "matchDate": date, "homeTeam": h, "awayTeam": a, "allHomeTeam": h, "allAwayTeam": a,
            "sectionsNo999": score, "poolStatus": pool}


def lv(h, a, status="finished"):
    return {"status": status, "status_code": "full", "goals": {"home": h, "away": a}}


def test_short_code():
    assert R.short_code("周六001") == "六001"
    assert R.short_code("六001") == "六001"
    assert R.short_code(None) is None


def test_sporttery_lookup_next_day_and_name_guard():
    m = sp("周四002", "2026-10-09", "库奥皮奥", "AC奥卢")
    table = {("2026-10-09", "四002"): m}
    assert R.sporttery_lookup(table, "2026-10-08", "四002", "古比斯", "AC奥卢") is m
    assert R.sporttery_lookup(table, "2026-10-08", "四002", "中国", "韩国") is None
    assert R.sporttery_lookup(table, "2026-10-06", "四002") is None


def test_decide_agree_single_conflict():
    d = R.decide(sp("周六001", "2026-10-10", "a", "b", "2:1"), lv(2, 1))
    assert d["action"] == "write" and d["score"] == (2, 1) and d["source"] == "sporttery+5df_csl"
    assert R.decide(None, lv(0, 0))["source"] == "5df_csl"
    assert R.decide(sp("周六001", "x", "a", "b", "1:1"), None)["source"] == "sporttery"
    c = R.decide(sp("周六001", "x", "a", "b", "1:1"), lv(2, 1))
    assert c["action"] == "flag" and c["flags"][0][0] == "score_conflict"


def test_decide_refund_postponed_and_wait():
    assert R.decide(sp("周六006", "x", "a", "b", "无效场次", "Refund"), None)["flags"][0][0] == "refund"
    assert R.decide(None, lv(None, None, "postponed"))["flags"][0][0] == "postponed"
    assert R.decide(None, lv(None, None, "in_play"))["action"] == "wait"
    assert R.decide(sp("周六001", "x", "a", "b", ""), None)["action"] == "wait"


def test_due_schedule():
    ko = dt.datetime(2026, 10, 10, 13, 0, tzinfo=TZ)
    m = {"kickoff_at": ko.isoformat()}
    assert not R.due(m, None, ko + dt.timedelta(hours=2, minutes=59))
    assert R.due(m, None, ko + dt.timedelta(hours=3))
    last = (ko + dt.timedelta(hours=3)).isoformat()
    assert not R.due(m, last, ko + dt.timedelta(hours=3, minutes=29))
    assert R.due(m, last, ko + dt.timedelta(hours=3, minutes=30))
    assert not R.due(m, None, ko + dt.timedelta(hours=6, minutes=1))


def test_check_db_path_refuses_production(tmp_path):
    with pytest.raises(SystemExit):
        R.check_db_path(R.PROD_DB)
    with pytest.raises(SystemExit):
        R.check_db_path(str(tmp_path / "app.db"))


def test_apply_inserts_only_missing_and_backs_up(tmp_path):
    db = mkdb(tmp_path)
    now = dt.datetime(2026, 10, 14, 16, 0, tzinfo=TZ)
    m1 = {"id": 1}
    m2 = {"id": 2}
    w = {"action": "write", "score": (0, 0), "source": "5df_csl", "fivedf_score": (0, 0), "flags": []}
    r = R.apply(db, [(m1, w), (m2, dict(w, score=(1, 0)))], tmp_path / "bk", "test", now)
    assert r["inserted"] == 1 and Path(r["backup"], "app.db").exists()
    c = sqlite3.connect(db)
    assert c.execute("select home_goals, away_goals, wdl from results where match_id = 1").fetchone() == (0, 0, "平")
    assert c.execute("select home_goals from results where match_id = 2").fetchone() == (9,)  # 已有行不覆盖
    assert c.execute("select source from result_ingest_log where match_id = 1").fetchone() == ("5df_csl",)


def test_apply_flags_and_no_write_when_nothing(tmp_path):
    db = mkdb(tmp_path)
    now = dt.datetime(2026, 10, 14, 16, 0, tzinfo=TZ)
    assert R.apply(db, [({"id": 1}, {"action": "wait", "flags": []})], tmp_path / "bk", "t", now)["backup"] is None
    R.apply(db, [({"id": 1}, {"action": "flag", "flags": [("score_conflict", {"a": 1})]})], tmp_path / "bk", "t", now)
    R.apply(db, [({"id": 1}, {"action": "flag", "flags": [("score_conflict", {"a": 2})]})], tmp_path / "bk", "t",
            now + dt.timedelta(minutes=30))
    c = sqlite3.connect(db)
    assert c.execute("select count(*) from results where match_id = 1").fetchone() == (0,)
    rows = c.execute("select flag, detail_json, first_seen_at, last_seen_at from result_ingest_flags").fetchall()
    assert len(rows) == 1 and '"a": 2' in rows[0][1] and rows[0][2] != rows[0][3]


def test_pending_matches_window(tmp_path):
    db = mkdb(tmp_path)
    c = sqlite3.connect(db)
    ko = dt.datetime(2026, 10, 10, 13, 0, tzinfo=TZ)
    assert [m["id"] for m in R.pending_matches(c, ko + dt.timedelta(hours=4), None, False)] == [1]
    assert R.pending_matches(c, ko + dt.timedelta(hours=7), None, False) == []
    assert [m["id"] for m in R.pending_matches(c, ko + dt.timedelta(hours=70), None, True)] == [1]


def test_daily_summary(tmp_path):
    db = mkdb(tmp_path)
    now = dt.datetime(2026, 10, 10, 20, 0, tzinfo=TZ)
    R.apply(db, [({"id": 1}, {"action": "flag", "flags": [("score_conflict", {})]})], tmp_path / "bk", "t", now)
    out = tmp_path / "daily_summary.md"
    R.write_daily_summary(db, out, now, days=1)
    row = [l for l in out.read_text(encoding="utf-8").splitlines() if l.startswith("| 2026-10-10")][0]
    assert row.split("|")[2].strip() == "0" and row.split("|")[3].strip() == "1" and row.split("|")[4].strip() == "1"


def test_prune_backups_keeps_recent_and_daily_first(tmp_path):
    for ts in ["20261012T010000", "20261013T010000", "20261013T020000", "20261013T030000", "20261013T040000"]:
        (tmp_path / f"v2d3-before-result-ingest-{ts}").mkdir()
    (tmp_path / "v2d3-before-other-20261013T050000").mkdir()
    R.prune_backups(tmp_path, keep=2)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["v2d3-before-other-20261013T050000", "v2d3-before-result-ingest-20261012T010000",
                    "v2d3-before-result-ingest-20261013T010000", "v2d3-before-result-ingest-20261013T030000",
                    "v2d3-before-result-ingest-20261013T040000"]


def test_daemon_single_instance_lock(tmp_path):
    db = mkdb(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    f = open(out / "daemon.lock", "w")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    p = subprocess.run([sys.executable, R.__file__, "daemon", "--db", db, "--out-dir", str(out)],
                       capture_output=True, text=True, timeout=30,
                       env={"FIVEDOLLAR_FOOTBALL_API_KEY": "test", "PATH": "/usr/bin:/bin"})
    assert p.returncode != 0 and "已有一份" in p.stderr
