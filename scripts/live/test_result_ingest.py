"""赛果定时入库的单元测试（只用临时库，不碰任何真实库）。"""
import datetime as dt
# 与 result_ingest 相同的跨平台文件锁，避免测试文件在 Windows 上因缺少 fcntl 而无法收集。
import importlib.util as _pl_util
import json
import pathlib as _pl_pathlib
_pl_spec = _pl_util.spec_from_file_location(
    "_portable_lock", _pl_pathlib.Path(__file__).resolve().parents[2] / "api/app/portable_lock.py")
fcntl = _pl_util.module_from_spec(_pl_spec)
_pl_spec.loader.exec_module(fcntl)
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


def test_check_db_path_refuses_production(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "FORMAL_MERGED", False)  # 旧语义：未合并时拒写正式库
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


# ---------------------------------------------------------------- 竞彩网缓存刷新（大陆本机文件导入。优先使用 YPC，即 YuePC；失败再用 MSI）

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
    r = R.import_sporttery_bundle(None, cache, out, now, "大陆本机均不可用")
    assert not r["ok"] and "大陆本机均不可用" in r["error"]
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


def test_crosscheck_sporttery_only_is_single_source(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "TEAMS_CSV", str(tmp_path / "none.csv"))
    db, out = _fd_setup(tmp_path)
    c = sqlite3.connect(db)
    c.execute("insert into matches values (5,'e','jingcai','2026-10-10','六005','圣保罗','维多利亚','2026-10-10T08:00:00+08:00')")
    c.execute("insert into results values (5, 2, 1, 3, '胜')")
    c.execute("insert into result_ingest_log values (5, 'sporttery', '2:1', null, null, '{}', null, 'x')")
    c.commit()
    c.close()
    now = dt.datetime(2026, 10, 11, 9, 0, tzinfo=TZ)
    r = R.crosscheck_single_source(db, out, tmp_path / "bk", now)
    assert r["single_source"] == 2
    R.write_daily_summary(db, out / "daily_summary.md", now, days=1)
    txt = (out / "daily_summary.md").read_text(encoding="utf-8")
    assert "| 单一来源 | 六005 | 2026-10-10 | 圣保罗 对 维多利亚 | 2-1 | 只有竞彩网 |" in txt
    assert "| 单一来源 | 六004 | 2026-10-10 | 某甲 对 某乙 | 1-0 | 只有 5DF |" in txt


def test_summary_lists_fd_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "TEAMS_CSV", str(tmp_path / "none.csv"))
    db, out = _fd_setup(tmp_path)
    (out / "fd_raw" / "new_FIN.csv").write_text(
        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res\nFinland,Veikkausliiga,2026,10/10/2026,18:00,Inter Turku,KuPS,2,0,H\n",
        encoding="utf-8")
    now = dt.datetime(2026, 10, 11, 9, 0, tzinfo=TZ)
    R.crosscheck_single_source(db, out, tmp_path / "bk", now)
    R.write_daily_summary(db, out / "daily_summary.md", now, days=1)
    assert "| 与 Football-Data.co.uk 不一致 | 六003 |" in (out / "daily_summary.md").read_text(encoding="utf-8")


def test_sporttery_import_dry_run_records_nothing(tmp_path):
    now = dt.datetime(2026, 10, 15, 10, 0, tzinfo=TZ)
    out = tmp_path / "out"
    out.mkdir()
    r = R.import_sporttery_bundle(None, tmp_path / "cache", out, now, "干跑", dry_run=True)
    assert r["dry_run"] and not r["ok"] and R.refresh_records(out) == []
    r = R.import_sporttery_bundle(_bundle(tmp_path, now), tmp_path / "cache", out, now, dry_run=True)
    assert r["ok"] and not (tmp_path / "cache").exists() and R.refresh_records(out) == []


def test_check_db_path_allows_production_after_merge(monkeypatch):
    monkeypatch.setattr(R, "FORMAL_MERGED", True)  # 合并后模式：正式库是唯一库
    assert R.check_db_path(R.PROD_DB)


# ---------------------------------------------------------------- 竞彩网二次核对（只用临时库）

CROSSCHECK_NOW = dt.datetime(2026, 10, 17, 9, 30, tzinfo=TZ)


def _seed_single_source(db, match_id, jc_id, home, away, score=(1, 0), *, flag=True, jingcai="2026-10-10"):
    """在临时库里放一场已经入库、来源为 5df_csl 的比赛。默认带未解决的单一来源标记。"""
    home_goals, away_goals = score
    outcome = "胜" if home_goals > away_goals else ("平" if home_goals == away_goals else "负")
    connection = sqlite3.connect(db)
    R.ensure_schema(connection)
    connection.execute(
        "insert into matches(id, match_uid, scope, jingcai_date, jc_id, home_team, away_team, kickoff_at) "
        "values (?, ?, 'jingcai', ?, ?, ?, ?, ?)",
        (match_id, f"uid{match_id}", jingcai, jc_id, home, away, f"{jingcai}T13:00:00+08:00"))
    connection.execute(
        "insert into results values (?, ?, ?, ?, ?)",
        (match_id, home_goals, away_goals, home_goals + away_goals, outcome))
    connection.execute(
        "insert into result_ingest_log values (?, '5df_csl', null, ?, null, '{}', null, 'x')",
        (match_id, f"{home_goals}:{away_goals}"))
    if flag:
        connection.execute(
            "insert into result_ingest_flags values (?, 'single_source', '{}', 'x', 'x', null)",
            (match_id,))
    connection.commit()
    connection.close()


def _write_msi_cache(directory: Path, rows: list[dict]) -> None:
    """按竞彩网导入程序的分页格式写一份临时缓存。"""
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"errorCode": "0", "value": {"matchResult": rows}}
    (directory / "msi_2026-10-06_2026-10-11_p1.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _crosscheck(db: str, cache: Path, root: Path, apply: bool, approve=None):
    """对临时库执行一次竞彩网二次核对。approve 是分析师逐场放行的兜底清单。"""
    return R.run_sporttery_crosscheck(
        db, [str(cache)], root / "bk", root / "out", CROSSCHECK_NOW,
        apply=apply, approve_fallback=approve)


def _stored_state(db: str, match_id: int):
    """读出一场的来源、赛果表比分和单一来源标记，用来对比命令有没有改库。"""
    connection = sqlite3.connect(db)
    log = connection.execute(
        "select source, sporttery_score, fivedf_score, fd_score, detail_json "
        "from result_ingest_log where match_id = ?", (match_id,)).fetchone()
    score = connection.execute(
        "select home_goals, away_goals, total_goals, wdl from results where match_id = ?",
        (match_id,)).fetchone()
    flag = connection.execute(
        "select resolved_at from result_ingest_flags where match_id = ? and flag = 'single_source'",
        (match_id,)).fetchone()
    connection.close()
    return log, score, flag


def test_sporttery_crosscheck_upgrades_when_score_agrees(tmp_path):
    db = mkdb(tmp_path)
    _seed_single_source(db, 11, "六011", "上海海港", "山东泰山", (2, 1))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六011", "2026-10-11", "上海海港", "山东泰山", "2:1")])
    result = _crosscheck(db, cache, tmp_path, True)
    assert result["wrote"] is True
    assert (result["upgrade_count"], result["mismatch_count"], result["no_data_count"]) == (1, 0, 0)
    assert Path(result["backup"], "app.db").exists()
    audit = json.loads(Path(result["audit"]).read_text(encoding="utf-8"))
    assert audit["kind"] == "sporttery_crosscheck_audit"
    assert audit["upgrades"][0]["before"]["source"] == "5df_csl"
    assert audit["upgrades"][0]["before"]["sporttery_score"] is None
    assert audit["upgrades"][0]["after"]["source"] == "sporttery+5df_csl"
    assert audit["upgrades"][0]["after"]["sporttery_score"] == "2:1"
    assert audit["upgrades"][0]["after"]["single_source_resolved_at"] == R.iso(CROSSCHECK_NOW)
    assert audit["upgrades"][0]["match_method"] == "文本"
    assert result["fallback_pending_review_count"] == 0
    assert (Path(result["backup"]) / "sporttery_crosscheck_audit.json").read_text(encoding="utf-8")
    log, score, flag = _stored_state(db, 11)
    assert log == ("sporttery+5df_csl", "2:1", "2:1", None, "{}")
    assert score == (2, 1, 3, "胜")
    assert flag == (R.iso(CROSSCHECK_NOW),)
    backup_db = sqlite3.connect(str(Path(result["backup"]) / "app.db"))
    assert backup_db.execute("select source, sporttery_score from result_ingest_log where match_id = 11").fetchone() == (
        "5df_csl", None)
    backup_db.close()


def test_sporttery_crosscheck_leaves_mismatch_untouched(tmp_path):
    db = mkdb(tmp_path)
    _seed_single_source(db, 12, "六012", "北京国安", "广州队", (1, 0))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六012", "2026-10-10", "北京国安", "广州队", "0:1")])
    before = _stored_state(db, 12)
    result = _crosscheck(db, cache, tmp_path, True)
    assert result["mismatch_count"] == 1 and result["upgrade_count"] == 0 and result["wrote"] is False
    assert result["backup"] is None and result["audit"] is None
    assert _stored_state(db, 12) == before
    assert "两边不一致，本场不修改" in result["mismatch_matches"][0]["reason"]


def test_sporttery_crosscheck_leaves_missing_cache_untouched(tmp_path):
    db = mkdb(tmp_path)
    _seed_single_source(db, 13, "六013", "成都蓉城", "武汉三镇", (3, 2))
    cache = tmp_path / "cache"
    cache.mkdir()
    before = _stored_state(db, 13)
    result = _crosscheck(db, cache, tmp_path, True)
    assert result["no_data_count"] == 1 and result["upgrade_count"] == 0 and result["wrote"] is False
    assert result["backup"] is None
    assert _stored_state(db, 13) == before


def test_sporttery_crosscheck_rejects_team_name_mismatch(tmp_path):
    """队名文本对不上、又没有球队编号时，比分一致只列入兜底待复核，默认写入不升级。"""
    db = mkdb(tmp_path)
    _seed_single_source(db, 14, "六014", "天津津门虎", "梅州客家", (1, 0))
    _seed_single_source(db, 15, "六015", "上海海港", "甲乙丙丁", (1, 0))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [
        sp("周六014", "2026-10-10", "上海 海港", "山东 泰山", "1:0"),
        sp("周六015", "2026-10-10", "上海海港", "山东泰山", "1:0"),
    ])
    before_14 = _stored_state(db, 14)
    before_15 = _stored_state(db, 15)
    result = _crosscheck(db, cache, tmp_path, True)
    assert result["upgrade_count"] == 0 and result["fallback_pending_review_count"] == 2
    assert result["no_data_count"] == 0 and result["wrote"] is False and result["backup"] is None
    assert _stored_state(db, 14) == before_14
    assert _stored_state(db, 15) == before_15
    assert all(item["match_method"] == "兜底" for item in result["fallback_pending_review_matches"])
    assert all("兜底待复核" in item["reason"] for item in result["fallback_pending_review_matches"])


def test_sporttery_crosscheck_dry_run_does_not_write(tmp_path):
    db = mkdb(tmp_path)
    _seed_single_source(db, 11, "六011", "上海海港", "山东泰山", (2, 1))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六011", "2026-10-11", "上海海港", "山东泰山", "2:1")])
    before = _stored_state(db, 11)
    result = _crosscheck(db, cache, tmp_path, False)
    assert result["applied"] is False and result["wrote"] is False and result["upgrade_count"] == 1
    assert result["backup"] is None and result["audit"] is None
    assert _stored_state(db, 11) == before
    assert not (tmp_path / "bk").exists()
    report = Path(result["report"]).read_text(encoding="utf-8")
    assert "将升级" in report and "本次没有修改数据库" in report


def test_sporttery_crosscheck_apply_is_idempotent(tmp_path):
    db = mkdb(tmp_path)
    _seed_single_source(db, 11, "六011", "上海海港", "山东泰山", (2, 1))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六011", "2026-10-11", "上海海港", "山东泰山", "2:1")])
    first = _crosscheck(db, cache, tmp_path, True)
    assert first["wrote"] is True
    snapshot = _stored_state(db, 11)
    second = _crosscheck(db, cache, tmp_path, True)
    assert second["checked"] == 0 and second["upgrade_count"] == 0 and second["wrote"] is False
    assert second["backup"] is None
    assert _stored_state(db, 11) == snapshot
    backups = [path for path in (tmp_path / "bk").iterdir() if path.name.startswith("v2d3-before-sporttery-crosscheck-")]
    assert backups == [Path(first["backup"])]


def test_sporttery_crosscheck_ignores_rows_without_open_single_source_flag(tmp_path):
    db = mkdb(tmp_path)
    _seed_single_source(db, 16, "六016", "上海海港", "山东泰山", (2, 1), flag=False)
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六016", "2026-10-10", "上海海港", "山东泰山", "2:1")])
    before = _stored_state(db, 16)
    result = _crosscheck(db, cache, tmp_path, True)
    assert result["checked"] == 0 and result["wrote"] is False
    assert _stored_state(db, 16) == before
    assert before[2] is None


def test_sporttery_crosscheck_does_not_upgrade_refund(tmp_path):
    db = mkdb(tmp_path)
    _seed_single_source(db, 17, "六017", "上海海港", "山东泰山", (1, 0))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六017", "2026-10-10", "上海海港", "山东泰山", "1:0", "Refund")])
    before = _stored_state(db, 17)
    result = _crosscheck(db, cache, tmp_path, True)
    assert result["mismatch_count"] == 1 and result["wrote"] is False
    assert _stored_state(db, 17) == before
    assert "退款" in result["mismatch_matches"][0]["reason"]


def test_sporttery_crosscheck_rollback_restores_and_second_run_changes_nothing(tmp_path):
    db = mkdb(tmp_path)
    _seed_single_source(db, 11, "六011", "上海海港", "山东泰山", (2, 1))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六011", "2026-10-11", "上海海港", "山东泰山", "2:1")])
    upgraded = _crosscheck(db, cache, tmp_path, True)
    audit = Path(upgraded["audit"])
    preview = R.rollback_sporttery_crosscheck(db, audit, tmp_path / "bk", tmp_path / "out", CROSSCHECK_NOW, apply=False)
    assert preview["restore_count"] == 1 and preview["wrote"] is False and preview["backup"] is None
    assert _stored_state(db, 11)[0][0] == "sporttery+5df_csl"
    restored = R.rollback_sporttery_crosscheck(db, audit, tmp_path / "bk", tmp_path / "out", CROSSCHECK_NOW, apply=True)
    assert restored["wrote"] is True and restored["backup"]
    log, score, flag = _stored_state(db, 11)
    assert log == ("5df_csl", None, "2:1", None, "{}")
    assert score == (2, 1, 3, "胜")
    assert flag == (None,)
    again = R.rollback_sporttery_crosscheck(db, audit, tmp_path / "bk", tmp_path / "out", CROSSCHECK_NOW, apply=True)
    assert again["restore_count"] == 0 and again["wrote"] is False and again["backup"] is None
    assert _stored_state(db, 11) == (log, score, flag)


def test_sporttery_crosscheck_rollback_skips_when_stored_score_changed(tmp_path):
    db = mkdb(tmp_path)
    _seed_single_source(db, 11, "六011", "上海海港", "山东泰山", (2, 1))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六011", "2026-10-11", "上海海港", "山东泰山", "2:1")])
    upgraded = _crosscheck(db, cache, tmp_path, True)
    connection = sqlite3.connect(db)
    connection.execute("update results set home_goals = 9, total_goals = 10 where match_id = 11")
    connection.commit()
    connection.close()
    result = R.rollback_sporttery_crosscheck(
        db, Path(upgraded["audit"]), tmp_path / "bk", tmp_path / "out", CROSSCHECK_NOW, apply=True)
    assert result["skip_count"] == 1 and result["restore_count"] == 0 and result["wrote"] is False
    assert _stored_state(db, 11)[0][0] == "sporttery+5df_csl"
    assert "赛果表里的比分已经与审计时不同" in result["skip_matches"][0]["reason"]


def test_sporttery_crosscheck_cli_preview_does_not_write_database(tmp_path, capsys):
    db = mkdb(tmp_path)
    _seed_single_source(db, 11, "六011", "上海海港", "山东泰山", (2, 1))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六011", "2026-10-11", "上海海港", "山东泰山", "2:1")])
    before = _stored_state(db, 11)
    R.main([
        "sporttery_crosscheck", "--db", db, "--out-dir", str(tmp_path / "out"),
        "--sporttery-dir", str(cache), "--sporttery-cache", str(cache),
        "--backup-root", str(tmp_path / "bk"),
    ])
    printed = capsys.readouterr().out
    assert "将升级" in printed and "本次没有修改数据库" in printed
    assert _stored_state(db, 11) == before
    assert not (tmp_path / "bk").exists()


def test_sporttery_crosscheck_help_describes_preview_and_rollback(capsys):
    with pytest.raises(SystemExit) as caught:
        R.main(["--help"])
    assert caught.value.code == 0
    text = capsys.readouterr().out
    assert "竞彩网二次核对" in text
    assert "--apply" in text and "--rollback" in text and "--approve-fallback" in text
    assert "不修改数据库" in text
    assert "兜底待复核" in text
    assert "竞彩日+短编号" in text


def _install_team_tables(db: str) -> None:
    """在临时库里补上球队编号列、规范名表和别名表。调用前应先写入场次，避免按位置插入时列数对不上。"""
    connection = sqlite3.connect(db)
    columns = {row[1] for row in connection.execute("pragma table_info(matches)")}
    if "home_team_id" not in columns:
        connection.execute("alter table matches add column home_team_id integer")
    if "away_team_id" not in columns:
        connection.execute("alter table matches add column away_team_id integer")
    connection.execute("create table if not exists teams (id integer primary key, name_zh_canonical text)")
    connection.execute(
        "create table if not exists team_aliases ("
        "id integer primary key autoincrement, alias text not null, team_id integer not null, source text)")
    connection.commit()
    connection.close()


def _save_team(db: str, team_id: int, canonical: str, aliases: list[str]) -> None:
    """写入一支球队的规范名和别名。这一步还不把场次的球队编号指过去。"""
    connection = sqlite3.connect(db)
    connection.execute(
        "insert or replace into teams(id, name_zh_canonical) values (?, ?)",
        (team_id, canonical))
    for alias in aliases:
        connection.execute(
            "insert into team_aliases(alias, team_id, source) values (?, ?, ?)",
            (alias, team_id, "单元测试"))
    connection.commit()
    connection.close()


def _bind_team(db: str, match_id: int, side: str, team_id: int, canonical: str, aliases: list[str]) -> None:
    """把一场的主队或客队编号指到规范名，并写入该队的全部别名。"""
    if side == "home":
        column = "home_team_id"
    elif side == "away":
        column = "away_team_id"
    else:
        raise AssertionError("测试只能绑定主队或客队。")
    _save_team(db, team_id, canonical, aliases)
    connection = sqlite3.connect(db)
    connection.execute(f"update matches set {column} = ? where id = ?", (team_id, match_id))
    connection.commit()
    connection.close()


def test_sporttery_crosscheck_alias_match_upgrades(tmp_path):
    """队名文本对不上时，按球队编号比对规范名和别名，对上并且比分一致就升级。"""
    db = mkdb(tmp_path)
    _seed_single_source(db, 61, "六061", "甲乙丙丁", "戊己庚辛", (2, 0))
    _install_team_tables(db)
    # 这里使用全角拉丁字母。不做名称规范时，它们和竞彩网的半角 AC 没有共同的英文字母。
    fullwidth_ac = "\uff21\uff23"
    _bind_team(db, 61, "home", 901, "无关名称甲", [fullwidth_ac])
    _bind_team(db, 61, "away", 902, "山东泰山", [])
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六061", "2026-10-10", "AC", "山东泰山", "2:0")])
    result = _crosscheck(db, cache, tmp_path, True)
    assert result["wrote"] is True
    assert result["upgrade_count"] == 1 and result["fallback_pending_review_count"] == 0
    assert result["upgrade_matches"][0]["match_method"] == "别名"
    audit = json.loads(Path(result["audit"]).read_text(encoding="utf-8"))
    assert audit["upgrades"][0]["match_method"] == "别名"
    log, score, flag = _stored_state(db, 61)
    assert log == ("sporttery+5df_csl", "2:0", "2:0", None, "{}")
    assert score == (2, 0, 2, "胜")
    assert flag == (R.iso(CROSSCHECK_NOW),)


def test_sporttery_crosscheck_fallback_is_pending_and_apply_does_not_upgrade(tmp_path):
    """同竞彩日、同短编号、比分一致但队名对不上时，单独标成兜底待复核，默认写入不升级。"""
    db = mkdb(tmp_path)
    _seed_single_source(db, 71, "六071", "甲乙丙丁", "戊己庚辛", (1, 0))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六071", "2026-10-10", "上海海港", "山东泰山", "1:0")])
    before = _stored_state(db, 71)
    preview = _crosscheck(db, cache, tmp_path, False)
    assert preview["applied"] is False and preview["wrote"] is False and preview["backup"] is None
    assert preview["upgrade_count"] == 0 and preview["fallback_pending_review_count"] == 1
    pending = preview["fallback_pending_review_matches"][0]
    assert pending["category"] == "fallback_pending_review" and pending["match_method"] == "兜底"
    report = Path(preview["report"]).read_text(encoding="utf-8")
    assert "兜底待复核" in report and "本次没有修改数据库" in report
    assert _stored_state(db, 71) == before
    applied = _crosscheck(db, cache, tmp_path, True)
    assert applied["wrote"] is False and applied["backup"] is None and applied["audit"] is None
    assert applied["upgrade_count"] == 0 and applied["fallback_pending_review_count"] == 1
    assert _stored_state(db, 71) == before


def test_sporttery_crosscheck_approve_fallback_upgrades_only_listed_matches(tmp_path, capsys):
    """只有放行清单里的兜底场次才会升级，没有写进清单的场次保持原样。"""
    db = mkdb(tmp_path)
    _seed_single_source(db, 81, "六081", "甲乙丙丁", "戊己庚辛", (3, 1))
    _seed_single_source(db, 82, "六082", "子丑寅卯", "辰巳午未", (1, 1))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [
        sp("周六081", "2026-10-10", "上海海港", "山东泰山", "3:1"),
        sp("周六082", "2026-10-10", "北京国安", "广州队", "1:1"),
    ])
    approval = tmp_path / "放行清单.txt"
    approval.write_text(
        "# 分析师只放行下面这一场\n\n2026-10-10+六081\n",
        encoding="utf-8")
    before_82 = _stored_state(db, 82)
    preview = _crosscheck(db, cache, tmp_path, False, approve=[str(approval)])
    assert preview["wrote"] is False and preview["backup"] is None
    assert preview["upgrade_count"] == 1 and preview["fallback_pending_review_count"] == 1
    assert preview["upgrade_matches"][0]["match_id"] == 81
    assert preview["upgrade_matches"][0]["match_method"] == "兜底"
    assert preview["fallback_pending_review_matches"][0]["match_id"] == 82
    assert _stored_state(db, 81)[0][0] == "5df_csl"
    R.main([
        "sporttery_crosscheck", "--apply", "--db", db,
        "--out-dir", str(tmp_path / "cli-out"),
        "--sporttery-dir", str(cache), "--sporttery-cache", str(cache),
        "--backup-root", str(tmp_path / "cli-bk"),
        "--approve-fallback", str(approval),
    ])
    printed = capsys.readouterr().out
    assert "已升级" in printed and "这场的匹配方式是兜底" in printed
    assert "兜底待复核" in printed
    log, score, flag = _stored_state(db, 81)
    assert log[0] == "sporttery+5df_csl" and log[1] == "3:1"
    assert score == (3, 1, 4, "胜")
    assert flag[0]
    assert _stored_state(db, 82) == before_82
    audit_path = next((tmp_path / "cli-out").glob("sporttery_crosscheck_audit_*.json"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["upgrades"][0]["match_id"] == 81
    assert audit["upgrades"][0]["match_method"] == "兜底"
    restored = R.rollback_sporttery_crosscheck(
        db, audit_path, tmp_path / "cli-bk", tmp_path / "cli-out", CROSSCHECK_NOW, apply=True)
    assert restored["wrote"] is True
    assert _stored_state(db, 81)[0][0] == "5df_csl"
    assert _stored_state(db, 81)[2] == (None,)
    assert _stored_state(db, 82) == before_82


def test_sporttery_crosscheck_fallback_score_mismatch_is_not_upgraded(tmp_path):
    """比分不一致时，即使同竞彩日、同短编号，并且已经写进放行清单，也不升级。"""
    db = mkdb(tmp_path)
    _seed_single_source(db, 91, "六091", "甲乙丙丁", "戊己庚辛", (1, 0))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六091", "2026-10-10", "上海海港", "山东泰山", "0:1")])
    before = _stored_state(db, 91)
    result = _crosscheck(db, cache, tmp_path, True, approve=["2026-10-10+周六091"])
    assert result["upgrade_count"] == 0 and result["fallback_pending_review_count"] == 0
    assert result["mismatch_count"] == 1 and result["wrote"] is False and result["backup"] is None
    assert result["mismatch_matches"][0]["match_method"] == "兜底"
    assert "兜底也不升级" in result["mismatch_matches"][0]["reason"]
    assert "2026-10-10+六091" in result["unused_fallback_approvals"]
    assert _stored_state(db, 91) == before


def test_sporttery_crosscheck_empty_team_id_skips_alias_and_stays_fallback(tmp_path):
    """球队编号为空时跳过别名。别名表里即使有能对上的名称，也只进入兜底待复核。"""
    db = mkdb(tmp_path)
    _seed_single_source(db, 101, "六101", "甲乙丙丁", "戊己庚辛", (2, 0))
    _install_team_tables(db)
    _save_team(db, 801, "上海海港", ["上海海港"])
    _save_team(db, 802, "山东泰山", ["山东泰山"])
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六101", "2026-10-10", "上海海港", "山东泰山", "2:0")])
    before = _stored_state(db, 101)
    result = _crosscheck(db, cache, tmp_path, True)
    assert result["upgrade_count"] == 0 and result["fallback_pending_review_count"] == 1
    assert result["wrote"] is False and result["backup"] is None
    pending = result["fallback_pending_review_matches"][0]
    assert pending["match_method"] == "兜底"
    assert pending["home_team_id"] is None and pending["away_team_id"] is None
    assert "球队编号为空" in pending["reason"] and "跳过别名" in pending["reason"]
    assert _stored_state(db, 101) == before


def test_sporttery_crosscheck_next_day_name_mismatch_is_not_fallback(tmp_path):
    """开赛次日才出现、队名又对不上的记录，不进入兜底，也不因为放行清单而升级。"""
    db = mkdb(tmp_path)
    _seed_single_source(db, 111, "六111", "甲乙丙丁", "戊己庚辛", (1, 0))
    cache = tmp_path / "cache"
    _write_msi_cache(cache, [sp("周六111", "2026-10-11", "上海海港", "山东泰山", "1:0")])
    before = _stored_state(db, 111)
    result = _crosscheck(db, cache, tmp_path, True, approve=["2026-10-10+六111"])
    assert result["no_data_count"] == 1
    assert result["upgrade_count"] == 0 and result["fallback_pending_review_count"] == 0
    assert result["wrote"] is False and result["backup"] is None
    assert "不能使用同竞彩日兜底" in result["no_data_matches"][0]["reason"]
    assert _stored_state(db, 111) == before
