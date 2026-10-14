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


# ---------------------------------------------------------------- 竞彩网缓存刷新（MSI 文件导入）

def _bundle(tmp, now, *, ok=True, age_h=0.5, pages=None):
    import json
    raw = json.dumps({"errorCode": "0", "value": {"pages": 1, "matchResult": [sp("周六001", "2026-10-10", "大阪樱花", "横滨水手")]}},
                     ensure_ascii=False)
    doc = {"fetched_at": R.iso(now - dt.timedelta(hours=age_h)), "begin": "2026-10-06", "end": "2026-10-10",
           "ok": ok, "error": None if ok else "HTTP 567", "pages": [raw] if pages is None else pages}
    p = tmp / "latest_bundle.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def test_sporttery_import_ok_stale_failed_missing(tmp_path):
    now = dt.datetime(2026, 10, 15, 10, 0, tzinfo=TZ)
    cache, out = tmp_path / "cache", tmp_path / "out"
    out.mkdir()
    r = R.import_sporttery_bundle(_bundle(tmp_path, now), cache, out, now)
    assert r["ok"] and r["matches"] == 1 and (cache / "msi_2026-10-06_2026-10-10_p1.json").exists()
    assert ("2026-10-10", "六001") in R.load_sporttery([str(cache)])
    assert not R.import_sporttery_bundle(_bundle(tmp_path, now, age_h=5), cache, out, now)["ok"]
    assert not R.import_sporttery_bundle(_bundle(tmp_path, now, ok=False), cache, out, now)["ok"]
    r = R.import_sporttery_bundle(None, cache, out, now, "MSI 不在线")
    assert not r["ok"] and "MSI 不在线" in r["error"]
    recs = R.refresh_records(out)
    assert [x["ok"] for x in recs] == [True, False, False, False]


def test_daily_summary_shows_refresh_and_single_source(tmp_path):
    db = mkdb(tmp_path)
    now = dt.datetime(2026, 10, 10, 23, 0, tzinfo=TZ)
    out = tmp_path / "out"
    out.mkdir()
    c = sqlite3.connect(db)
    R.ensure_schema(c)
    c.execute("insert into result_ingest_flags values (1, 'single_source', '{}', 'x', 'x', null)")
    c.commit()
    c.close()
    R.import_sporttery_bundle(_bundle(tmp_path, now), tmp_path / "cache", out, now)
    R.write_daily_summary(db, out / "daily_summary.md", now, days=1)
    line = [ln for ln in (out / "daily_summary.md").read_text(encoding="utf-8").splitlines() if ln.startswith("| 2026-10-10")][0]
    cells = [x.strip() for x in line.strip("|").split("|")]
    assert cells[5] == "1" and cells[6].startswith("成功（23:00，1 场）")


# ---------------------------------------------------------------- Football-Data.co.uk 核对、单一来源标记

def _fd_setup(tmp_path):
    db = mkdb(tmp_path)
    c = sqlite3.connect(db)
    R.ensure_schema(c)
    c.execute("insert into matches values (3,'c','jingcai','2026-10-10','六003','英特杜古','古比斯','2026-10-10T23:00:00+08:00')")
    c.execute("insert into matches values (4,'d','jingcai','2026-10-10','六004','某甲','某乙','2026-10-10T23:00:00+08:00')")
    for mid in (3, 4):
        c.execute("insert into results values (?, 1, 0, 1, '胜')", (mid,))
        c.execute("insert into result_ingest_log values (?, '5df_csl', null, '1:0', null, '{}', null, 'x')", (mid,))
    c.commit()
    c.close()
    out = tmp_path / "out"
    (out / "fd_raw").mkdir(parents=True)
    (out / "fd_raw" / "new_FIN.csv").write_text(
        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res\nFinland,Veikkausliiga,2026,10/10/2026,18:00,Inter Turku,KuPS,1,0,H\n",
        encoding="utf-8")
    (out / "fd_team_map.csv").write_text("中文,英文\n英特杜古,Inter Turku\n古比斯,KuPS\n", encoding="utf-8")
    return db, out


def test_crosscheck_confirms_and_flags_single_source(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "TEAMS_CSV", str(tmp_path / "none.csv"))
    db, out = _fd_setup(tmp_path)
    now = dt.datetime(2026, 10, 11, 9, 0, tzinfo=TZ)
    r = R.crosscheck_single_source(db, out, tmp_path / "bk", now)
    assert (r["confirmed"], r["single_source"], r["fd_score_mismatch"]) == (1, 1, 0) and r["backup"]
    c = sqlite3.connect(db)
    assert c.execute("select source, fd_score from result_ingest_log where match_id = 3").fetchone() == \
        ("5df_csl+football_data_co_uk", "1:0")
    assert c.execute("select flag from result_ingest_flags where match_id = 4 and resolved_at is null").fetchall() == [("single_source",)]
    c.close()
    # 第二次：没有变化就不再备份
    assert R.crosscheck_single_source(db, out, tmp_path / "bk", now)["backup"] is None


def test_crosscheck_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "TEAMS_CSV", str(tmp_path / "none.csv"))
    db, out = _fd_setup(tmp_path)
    (out / "fd_raw" / "new_FIN.csv").write_text(
        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res\nFinland,Veikkausliiga,2026,10/10/2026,18:00,Inter Turku,KuPS,2,0,H\n",
        encoding="utf-8")
    r = R.crosscheck_single_source(db, out, tmp_path / "bk", dt.datetime(2026, 10, 11, 9, 0, tzinfo=TZ))
    assert r["fd_score_mismatch"] == 1
