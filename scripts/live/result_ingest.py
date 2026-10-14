"""赛果定时入库（只写研究副本 v2d3，绝不写正式库）。

节奏按 docs/schema/v2_0-result-ingest-schedule.md（用户 2026-10-07 拍板）：
开赛后 3 小时主拉，之后每 30 分钟补拉一次，开赛后 6 小时截止；截止后仍没有赛果的场次挂日核对队列。

来源：
- 竞彩网赛果（sporttery，getUniformMatchResultV1 的原始分页 JSON）。本机出口通常被拦（HTTP 567），
  所以本程序只尝试直连一次，失败就读本地缓存目录（由国内出口机器刷新后放入）。
- 5DollarFootballAPI 的中国体彩清单 /chinasportslottery（自带完赛比分和状态），密钥只从环境变量
  FIVEDOLLAR_FOOTBALL_API_KEY 读取，不打印、不落盘。
- Football-Data.co.uk 公开 CSV（只在两个来源都没有比分时作兜底核对，由 fd_crosscheck 子命令执行）。

写库规则：
- 只插入 results 里缺失的行，不覆盖已有的行。
- 两个来源都有比分且不一致：不写，标记 score_conflict。
- 竞彩退款：标记 refund；5DF 推迟、取消、腰斩：标记 postponed、cancelled、abandoned；这些都不进可结算赛果。
- 每次有内容要写之前，先用 sqlite backup 备份到 /workspace/backups/v2d3-before-result-ingest-<时间戳>/app.db。
- 新增两张表（只追加，不改已有表）：result_ingest_log 记录每场赛果的来源，result_ingest_flags 记录特殊场次标记。
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import glob
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))
BASE_5DF = "https://api.5dollarfootballapi.com/v1"
SPORTTERY_URL = ("https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"
                 "?matchBeginDate={b}&matchEndDate={e}&leagueId=&pageSize=100&pageNo={p}&isFix=0&matchPage=1&pcOrWap=1")
PROD_DB = "/workspace/match-analysis-api/data/app.db"
MAIN_PULL_H, RETRY_MIN, CUTOFF_H = 3, 30, 6
GAP_SEC = 3.0
BACKUP_KEEP = 24

SCHEMA = """
CREATE TABLE IF NOT EXISTS result_ingest_log (
  match_id INTEGER PRIMARY KEY REFERENCES matches(id),
  source TEXT NOT NULL,            -- sporttery | 5df_csl | sporttery+5df_csl | football_data_co_uk
  sporttery_score TEXT, fivedf_score TEXT, fd_score TEXT,
  detail_json TEXT,
  batch_id INTEGER REFERENCES import_batches(id),
  ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS result_ingest_flags (
  match_id INTEGER NOT NULL REFERENCES matches(id),
  flag TEXT NOT NULL,              -- score_conflict | refund | postponed | cancelled | abandoned | no_result_after_cutoff
  detail_json TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  resolved_at TEXT,
  PRIMARY KEY (match_id, flag)
);
"""

WK = "一二三四五六日"


def now_cn() -> dt.datetime:
    return dt.datetime.now(TZ)


def iso(t: dt.datetime) -> str:
    return t.astimezone(TZ).isoformat(timespec="seconds")


def short_code(code: str | None) -> str | None:
    """'周六001' / '六001' -> '六001'。"""
    m = re.search(r"([一二三四五六日])(\d{3})", code or "")
    return m.group(1) + m.group(2) if m else None


def wdl(h: int, a: int) -> str:
    return "胜" if h > a else ("平" if h == a else "负")


def check_db_path(db: str) -> str:
    real = os.path.realpath(db)
    if real == os.path.realpath(PROD_DB) or not real.endswith(os.path.join("v2d3", "app.db")):
        raise SystemExit(f"拒绝写入：{db} 不是研究副本 v2d3（正式库绝对不能写）。")
    return real


# ----------------------------------------------------------------------------- 来源解析

def sporttery_score(mr: dict):
    m = re.match(r"^(\d+):(\d+)$", mr.get("sectionsNo999") or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def load_sporttery(dirs: list[str]) -> dict:
    """返回 {(开赛自然日 matchDate, 短编号): matchResult}；同一键后读到的文件覆盖先读到的（新缓存优先）。"""
    out: dict = {}
    files = []
    for d in dirs:
        files += sorted(glob.glob(os.path.join(d, "*.json")), key=os.path.getmtime)
    for f in files:
        try:
            v = json.load(open(f, encoding="utf-8")).get("value") or {}
        except Exception:
            continue
        for mr in v.get("matchResult") or []:
            s = short_code(mr.get("matchNumStr"))
            if s and mr.get("matchDate"):
                out[(mr["matchDate"][:10], s)] = mr
    return out


def load_csl(dirs: list[str]) -> dict:
    """5DF 清单文件 {jingcai_date, data:[...]} -> {(竞彩日, 短编号): item}；按文件修改时间，新的覆盖旧的。"""
    out: dict = {}
    files = []
    for d in dirs:
        files += [f for f in glob.glob(os.path.join(d, "*.json"))]
    for f in sorted(files, key=os.path.getmtime):
        try:
            doc = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        D = doc.get("jingcai_date") if isinstance(doc, dict) else None
        items = doc.get("data") if isinstance(doc, dict) else doc
        for it in items or []:
            num = ((it.get("lottery") or {}).get("jingcailottery") or {}).get("number")
            s = short_code(num)
            if s and D:
                out[(D, s)] = it
    return out


def sporttery_lookup(sp: dict, jingcai_date: str, code: str | None, home: str = "", away: str = ""):
    """竞彩网 matchDate 是开赛的自然日，不是竞彩日：先查竞彩日当天，再查次日（同一周内编号唯一）。
    队名做宽松核对：主客两队和库里队名一个相同的字都没有时，视为没对上（防止编号错配）。"""
    if not code:
        return None
    d0 = dt.date.fromisoformat(jingcai_date)
    for k in (d0, d0 + dt.timedelta(days=1)):
        mr = sp.get((k.isoformat(), code))
        if mr is None:
            continue
        if home and away and not (set(home) & set(mr.get("allHomeTeam", "") + mr.get("homeTeam", ""))) \
                and not (set(away) & set(mr.get("allAwayTeam", "") + mr.get("awayTeam", ""))):
            return None
        return mr
    return None


def csl_score(it: dict):
    g = it.get("goals") or {}
    if it.get("status") == "finished" and g.get("home") is not None and g.get("away") is not None:
        return (int(g["home"]), int(g["away"]))
    return None


def decide(sp: dict | None, lv: dict | None) -> dict:
    """单场判定。返回 {action: write|flag|wait, score, source, flags:[(flag, detail)]}。"""
    flags = []
    sps = sporttery_score(sp) if sp else None
    lvs = csl_score(lv) if lv else None
    if sp and sp.get("poolStatus") == "Refund":
        flags.append(("refund", {"sporttery_poolStatus": "Refund", "sporttery_score": sps}))
    if lv and lv.get("status") in ("postponed", "cancelled", "abandoned"):
        flags.append((lv["status"], {"5df_status": lv.get("status"), "5df_status_code": lv.get("status_code"),
                                     "5df_status_reason": lv.get("status_reason")}))
    if flags:
        return {"action": "flag", "flags": flags, "sporttery_score": sps, "fivedf_score": lvs}
    if sps and lvs and sps != lvs:
        return {"action": "flag", "sporttery_score": sps, "fivedf_score": lvs,
                "flags": [("score_conflict", {"sporttery": list(sps), "5df": list(lvs),
                                              "5df_status_code": lv.get("status_code")})]}
    if sps or lvs:
        src = "sporttery+5df_csl" if (sps and lvs) else ("sporttery" if sps else "5df_csl")
        return {"action": "write", "score": sps or lvs, "source": src, "sporttery_score": sps, "fivedf_score": lvs, "flags": []}
    return {"action": "wait", "flags": [], "sporttery_score": None, "fivedf_score": None}


# ----------------------------------------------------------------------------- 拉取

class FiveDF:
    def __init__(self, call_log: Path):
        self.key = os.environ.get("FIVEDOLLAR_FOOTBALL_API_KEY", "")
        if not self.key:
            raise SystemExit("缺少环境变量 FIVEDOLLAR_FOOTBALL_API_KEY。")
        self.call_log = call_log
        self.last = 0.0
        self.calls = 0

    def get(self, path: str, params: dict):
        code, h, body = -1, {}, b""
        for attempt in range(4):
            wait = GAP_SEC - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            req = urllib.request.Request(BASE_5DF + path + "?" + urllib.parse.urlencode(params),
                                         headers={"Authorization": "Bearer " + self.key, "Accept": "application/json"})
            try:
                r = urllib.request.urlopen(req, timeout=40)
                code, body, h = r.status, r.read(), r.headers
            except urllib.error.HTTPError as e:
                code, body, h = e.code, e.read(), e.headers
            except Exception as e:
                code, body, h = -1, str(e).encode(), {}
            self.last = time.time()
            self.calls += 1
            rem = h.get("X-RateLimit-Remaining") if h else None
            with self.call_log.open("a", encoding="utf-8") as f:
                f.write(f"{iso(now_cn())}\t5df\t{path}\t{params.get('start_time')}\t{params.get('page')}\t{code}\t"
                        f"{h.get('X-RateLimit-Limit') if h else None}\t{rem}\n")
            if rem not in (None, "") and int(rem) <= 8:
                time.sleep(61)
            if code == 429 or code >= 500 or code == -1:
                time.sleep(float((h.get("Retry-After") if h else None) or 10) + 1)
                continue
            return code, json.loads(body)
        return code, {"_error": "retries_exhausted"}


def fetch_csl_day(cl: FiveDF, D: str, out_dir: Path) -> dict:
    base = dt.datetime.strptime(D, "%Y-%m-%d").replace(tzinfo=TZ)
    wins = [(base + dt.timedelta(hours=10), base + dt.timedelta(hours=34, seconds=-1)),
            (base + dt.timedelta(hours=34), base + dt.timedelta(hours=40))]
    items = {}
    for ws, we in wins:
        page = 1
        while True:
            code, data = cl.get("/chinasportslottery", {"types": "jingcailottery", "start_time": int(ws.timestamp()),
                                                        "end_time": int(we.timestamp()), "per_page": 100,
                                                        "page": page, "lang": "zh-cn"})
            if code != 200 or not data.get("success"):
                raise RuntimeError(f"5DF 清单 {D} 请求失败 http={code}")
            for it in data.get("data") or []:
                items[it.get("id")] = it
            if not (data.get("pagination") or {}).get("has_more"):
                break
            page += 1
    doc = {"jingcai_date": D, "fetched_at": iso(now_cn()), "data": list(items.values())}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{D}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def try_fetch_sporttery(b: str, e: str, out_dir: Path, call_log: Path) -> bool:
    """直连竞彩网一次；被拦（例如 HTTP 567）就返回 False，改读本地缓存。"""
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    p = 1
    while True:
        req = urllib.request.Request(SPORTTERY_URL.format(b=b, e=e, p=p),
                                     headers={"User-Agent": ua, "Referer": "https://www.sporttery.cn/jc/zqsgkj/"})
        code, body = -1, b""
        try:
            r = urllib.request.urlopen(req, timeout=30)
            code, body = r.status, r.read()
        except urllib.error.HTTPError as ex:
            code = ex.code
        except Exception:
            code = -1
        with call_log.open("a", encoding="utf-8") as f:
            f.write(f"{iso(now_cn())}\tsporttery\t{b}..{e}\t\t{p}\t{code}\t\t\n")
        try:
            j = json.loads(body) if code == 200 else None
        except Exception:
            j = None
        if not j or str(j.get("errorCode")) != "0":
            return False
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"auto_{b}_{e}_p{p}.json").write_bytes(body)
        pages = int((j.get("value") or {}).get("pages") or 1)
        if p >= pages:
            return True
        p += 1
        time.sleep(3.5)


# ----------------------------------------------------------------------------- 写库

def backup(db: str, backup_root: Path, tag: str = "result-ingest") -> str:
    stamp = f"{now_cn():%Y%m%dT%H%M%S}"
    d = backup_root / f"v2d3-before-{tag}-{stamp}"
    n = 1
    while d.exists():  # 同一秒内第二次写：加序号，绝不覆盖已有备份
        d = backup_root / f"v2d3-before-{tag}-{stamp}{n:02d}"
        n += 1
    d.mkdir(parents=True, exist_ok=False)
    src = sqlite3.connect(db, timeout=60)
    dst = sqlite3.connect(str(d / "app.db"))
    src.backup(dst)
    dst.close()
    src.close()
    return str(d)


def prune_backups(backup_root: Path, tag: str = "result-ingest", keep: int = BACKUP_KEEP) -> list[str]:
    """只清理本程序自己的备份目录：保留最近 keep 份，外加每天第一份。"""
    ds = sorted(p for p in backup_root.glob(f"v2d3-before-{tag}-*") if p.is_dir())
    firsts = {}
    for p in ds:
        firsts.setdefault(p.name.rsplit("-", 1)[-1][:8], p)
    keepset = set(ds[-keep:]) | set(firsts.values())
    removed = []
    for p in ds:
        if p not in keepset:
            shutil.rmtree(p)
            removed.append(str(p))
    return removed


def ensure_schema(con: sqlite3.Connection):
    con.executescript(SCHEMA)


def pending_matches(con: sqlite3.Connection, now: dt.datetime, since: str | None, backfill: bool):
    q = ("select m.id, m.jingcai_date, m.jc_id, m.kickoff_at, m.scope, m.home_team, m.away_team "
         "from matches m left join results r on r.match_id = m.id where r.match_id is null and m.kickoff_at is not null")
    args = []
    if since:
        q += " and m.kickoff_at >= ?"
        args.append(since)
    out = []
    for row in con.execute(q, args):
        ko = dt.datetime.fromisoformat(row[3])
        age = now - ko
        if age < dt.timedelta(hours=MAIN_PULL_H):
            continue
        if not backfill and age > dt.timedelta(hours=CUTOFF_H):
            continue
        out.append(dict(zip(("id", "jingcai_date", "jc_id", "kickoff_at", "scope", "home", "away"), row), age_h=age.total_seconds() / 3600))
    return out


def apply(db: str, decisions: list[tuple[dict, dict]], backup_root: Path, batch_label: str, now: dt.datetime,
          dry_run: bool = False) -> dict:
    """decisions: [(match, decision)]。只插入缺失 results；标记 upsert 到 result_ingest_flags。"""
    writes = [(m, d) for m, d in decisions if d["action"] == "write"]
    flags = [(m, f, det) for m, d in decisions for f, det in d.get("flags", [])]
    res = {"inserted": 0, "flagged": len(flags), "backup": None, "batch_id": None}
    if dry_run or (not writes and not flags):
        return res
    res["backup"] = backup(db, backup_root)
    con = sqlite3.connect(db, timeout=60)
    try:
        ensure_schema(con)
        with con:
            cur = con.execute("insert into import_batches(source_file, month, row_count, status) values (?, ?, ?, 'ok')",
                              (batch_label, now.strftime("%y%m"), len(writes)))
            bid = cur.lastrowid
            res["batch_id"] = bid
            for m, d in writes:
                h, a = d["score"]
                c = con.execute("insert or ignore into results(match_id, home_goals, away_goals, total_goals, wdl) "
                                "values (?, ?, ?, ?, ?)", (m["id"], h, a, h + a, wdl(h, a)))
                if c.rowcount:
                    res["inserted"] += 1
                    con.execute("insert or ignore into result_ingest_log(match_id, source, sporttery_score, fivedf_score, "
                                "fd_score, detail_json, batch_id, ingested_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
                                (m["id"], d["source"], _s(d.get("sporttery_score")), _s(d.get("fivedf_score")),
                                 _s(d.get("fd_score")), json.dumps(d.get("detail") or {}, ensure_ascii=False), bid, iso(now)))
                    con.execute("update result_ingest_flags set resolved_at = ? where match_id = ? and "
                                "flag = 'no_result_after_cutoff' and resolved_at is null", (iso(now), m["id"]))
            for m, f, det in flags:
                con.execute("insert into result_ingest_flags(match_id, flag, detail_json, first_seen_at, last_seen_at) "
                            "values (?, ?, ?, ?, ?) on conflict(match_id, flag) do update set "
                            "detail_json = excluded.detail_json, last_seen_at = excluded.last_seen_at",
                            (m["id"], f, json.dumps(det, ensure_ascii=False), iso(now), iso(now)))
    finally:
        con.close()
    return res


def _s(t):
    return f"{t[0]}:{t[1]}" if t else None


# ----------------------------------------------------------------------------- 定时

class State:
    def __init__(self, path: Path):
        self.path = path
        self.d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"last_pull": {}}

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


def due(m: dict, last_pull: str | None, now: dt.datetime) -> bool:
    """开赛 +3h 起主拉；之后距上次拉取满 30 分钟再补拉；+6h 截止（pending_matches 已过滤）。"""
    ko = dt.datetime.fromisoformat(m["kickoff_at"])
    if now < ko + dt.timedelta(hours=MAIN_PULL_H) or now > ko + dt.timedelta(hours=CUTOFF_H):
        return False
    if not last_pull:
        return True
    return now - dt.datetime.fromisoformat(last_pull) >= dt.timedelta(minutes=RETRY_MIN)


def tick(args, st: State, cl: FiveDF | None, now: dt.datetime, log) -> dict:
    db = check_db_path(args.db)
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    cand = pending_matches(ro, now, args.since, backfill=False)
    expired = cutoff_expired(ro, now, args.since)
    ro.close()
    todo = [m for m in cand if due(m, st.d["last_pull"].get(str(m["id"])), now)]
    out = {"due": len(todo), "inserted": 0, "flagged": 0}
    if todo:
        days = sorted({m["jingcai_date"] for m in todo})
        if cl:
            for D in days:
                try:
                    fetch_csl_day(cl, D, Path(args.csl_dir))
                except Exception as e:
                    log(f"5DF 清单 {D} 拉取失败：{e}")
        b = (dt.date.fromisoformat(days[0]) - dt.timedelta(days=1)).isoformat()
        e = (dt.date.fromisoformat(days[-1]) + dt.timedelta(days=1)).isoformat()
        if not args.no_sporttery_fetch and not try_fetch_sporttery(b, e, Path(args.sporttery_dir), Path(args.call_log)):
            log("竞彩网直连被拦，改读本地缓存。")
        sp = load_sporttery([args.sporttery_dir] + args.extra_sporttery_dir)
        lv = load_csl([args.csl_dir])
        decs = []
        for m in todo:
            k = (m["jingcai_date"], short_code(m["jc_id"]))
            decs.append((m, decide(sporttery_lookup(sp, m["jingcai_date"], k[1], m["home"], m["away"]), lv.get(k))))
            st.d["last_pull"][str(m["id"])] = iso(now)
        r = apply(db, decs, Path(args.backup_root), "result-ingest-scheduler (sporttery + 5df_csl)", now)
        out.update(inserted=r["inserted"], flagged=r["flagged"], backup=r["backup"])
        if r["backup"]:
            prune_backups(Path(args.backup_root))
        for m, d in decs:
            log(f"场次 {m['id']} {m['jc_id']} {m['home']} 对 {m['away']}：{d['action']} "
                f"{_s(d.get('score'))} {[f for f, _ in d.get('flags', [])]}")
    if expired:
        r = apply(db, [(m, {"action": "flag", "flags": [("no_result_after_cutoff", {"kickoff_at": m["kickoff_at"]})]})
                       for m in expired if not st.d.setdefault("cutoff_flagged", {}).get(str(m["id"]))],
                  Path(args.backup_root), "result-ingest-scheduler cutoff flags", now)
        for m in expired:
            st.d["cutoff_flagged"][str(m["id"])] = iso(now)
        if r["backup"]:
            prune_backups(Path(args.backup_root))
    st.save()
    return out


def cutoff_expired(con, now, since):
    """已过开赛 +6h、仍没有赛果的场次（挂日核对队列）。只看最近 2 天内截止的，避免把历史缺口反复标。"""
    q = ("select m.id, m.jingcai_date, m.jc_id, m.kickoff_at, m.scope, m.home_team, m.away_team from matches m "
         "left join results r on r.match_id = m.id where r.match_id is null and m.kickoff_at is not null")
    out = []
    for row in con.execute(q):
        ko = dt.datetime.fromisoformat(row[3])
        if dt.timedelta(hours=CUTOFF_H) < now - ko <= dt.timedelta(hours=CUTOFF_H + 48) and (not since or row[3] >= since):
            out.append(dict(zip(("id", "jingcai_date", "jc_id", "kickoff_at", "scope", "home", "away"), row)))
    return out


def write_daily_summary(db: str, path: Path, now: dt.datetime, days: int = 14):
    """每天一节：当天补到几场（按写入时刻的自然日）、还缺几场（当天开赛、已过 +3h 仍没有赛果）、冲突几场（未解决）。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    has_log = con.execute("select 1 from sqlite_master where name = 'result_ingest_log'").fetchone()
    lines = ["# 赛果定时入库每日汇总", "",
             f"本文件由赛果定时入库程序自动生成，最后更新时间是 {iso(now)}（北京时间）。",
             "「补到」是当天写入研究副本 v2d3 的赛果场数；「还缺」是当天开赛、已过开赛后 3 小时但仍没有赛果的场数；"
             "「冲突」是当天开赛、两个来源比分不一致而没有写入的场数；「特殊」是推迟、取消、腰斩或退款的场数。", ""]
    lines += ["| 日期 | 补到 | 还缺 | 冲突 | 特殊 | 还缺的场次 |", "|---|---|---|---|---|---|"]
    for i in range(days):
        D = (now - dt.timedelta(days=i)).date()
        lo, hi = f"{D.isoformat()}T00:00:00+08:00", f"{(D + dt.timedelta(days=1)).isoformat()}T00:00:00+08:00"
        ins = con.execute("select count(*) from result_ingest_log where ingested_at >= ? and ingested_at < ?",
                          (lo, hi)).fetchone()[0] if has_log else 0
        miss = [r for r in con.execute(
            "select m.id, m.jc_id, m.home_team, m.away_team, m.kickoff_at from matches m left join results r on r.match_id = m.id "
            "where r.match_id is null and m.kickoff_at >= ? and m.kickoff_at < ?", (lo, hi))
            if now - dt.datetime.fromisoformat(r[4]) >= dt.timedelta(hours=MAIN_PULL_H)]
        conf = spec = 0
        if has_log:
            ids = [r[0] for r in miss]
            fl = con.execute("select f.match_id, f.flag from result_ingest_flags f join matches m on m.id = f.match_id "
                             "left join results r on r.match_id = m.id where r.match_id is null and f.resolved_at is null "
                             "and m.kickoff_at >= ? and m.kickoff_at < ?", (lo, hi)).fetchall()
            conf = len({a for a, f in fl if f == "score_conflict"})
            spec = len({a for a, f in fl if f in ("refund", "postponed", "cancelled", "abandoned")})
        names = "；".join(f"{r[1]} {r[2]} 对 {r[3]}" for r in miss[:12]) + ("等" if len(miss) > 12 else "")
        lines.append(f"| {D.isoformat()} | {ins} | {len(miss)} | {conf} | {spec} | {names or '无'} |")
    con.close()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def daemon(args):
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lockf = open(out_dir / "daemon.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("已有一份赛果定时入库程序在运行（daemon.lock 被占用），本次不启动。")
    lockf.write(str(os.getpid()))
    lockf.flush()
    st = State(out_dir / "state.json")
    cl = FiveDF(Path(args.call_log))

    def log(msg):
        with (out_dir / "daemon.log").open("a", encoding="utf-8") as f:
            f.write(f"{iso(now_cn())} {msg}\n")

    log(f"启动，进程号 {os.getpid()}，数据库 {check_db_path(args.db)}")
    last_summary = None
    while True:
        now = now_cn()
        try:
            r = tick(args, st, cl, now, log)
            if r["due"]:
                log(f"本轮到点 {r['due']} 场，写入 {r['inserted']} 场，标记 {r['flagged']} 条，备份 {r.get('backup')}")
        except Exception as e:  # 单轮失败不退出
            log(f"本轮出错：{type(e).__name__}: {e}")
        if last_summary is None or now - last_summary >= dt.timedelta(minutes=30):
            try:
                write_daily_summary(check_db_path(args.db), out_dir / "daily_summary.md", now)
                last_summary = now
            except Exception as e:
                log(f"汇总出错：{e}")
        (out_dir / "heartbeat.json").write_text(json.dumps({"pid": os.getpid(), "at": iso(now_cn()),
                                                            "api_calls_5df": cl.calls}), encoding="utf-8")
        time.sleep(args.interval)


def main(argv=None):
    p = argparse.ArgumentParser(description="赛果定时入库（只写研究副本 v2d3）")
    p.add_argument("cmd", choices=["daemon", "once", "backfill", "summary"])
    p.add_argument("--db", default=os.environ.get("V2D3_DB_PATH", "/workspace/match-analysis-api/data/v2d3/app.db"))
    p.add_argument("--out-dir", default="/workspace/odds-data/backfill/result-ingest-2026-10-14")
    p.add_argument("--backup-root", default="/workspace/backups")
    p.add_argument("--csl-dir", default=None)
    p.add_argument("--sporttery-dir", default=None)
    p.add_argument("--extra-sporttery-dir", action="append", default=[])
    p.add_argument("--call-log", default=None)
    p.add_argument("--since", default="2026-10-01")
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--no-sporttery-fetch", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    od = Path(a.out_dir)
    a.csl_dir = a.csl_dir or str(od / "csl_raw")
    a.sporttery_dir = a.sporttery_dir or str(od / "sporttery_raw")
    a.call_log = a.call_log or str(od / "call_log.tsv")
    db = check_db_path(a.db)
    if a.cmd == "daemon":
        daemon(a)
    elif a.cmd == "summary":
        write_daily_summary(db, od / "daily_summary.md", now_cn())
    elif a.cmd == "once":
        st = State(od / "state.json")
        print(tick(a, st, FiveDF(Path(a.call_log)), now_cn(), print))
    elif a.cmd == "backfill":
        # 补录：已过开赛 +3h 且没有赛果的场次，不受 +6h 截止限制；只用本地已刷新的缓存，不新调接口。
        now = now_cn()
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ms = pending_matches(ro, now, a.since, backfill=True)
        ro.close()
        sp = load_sporttery([a.sporttery_dir] + a.extra_sporttery_dir)
        lv = load_csl([a.csl_dir])
        decs = [(m, decide(sporttery_lookup(sp, m["jingcai_date"], short_code(m["jc_id"]), m["home"], m["away"]),
                           lv.get((m["jingcai_date"], short_code(m["jc_id"]))))) for m in ms]
        r = apply(db, decs, Path(a.backup_root), "result-ingest-backfill-20261014 (sporttery + 5df_csl)", now, a.dry_run)
        rep = [{"match_id": m["id"], "jingcai_date": m["jingcai_date"], "jc_id": m["jc_id"], "match": f"{m['home']} 对 {m['away']}",
                "kickoff_at": m["kickoff_at"], "scope": m["scope"], "action": d["action"], "score": _s(d.get("score")),
                "source": d.get("source"), "sporttery": _s(d.get("sporttery_score")), "5df": _s(d.get("fivedf_score")),
                "flags": d.get("flags")} for m, d in decs]
        (od / ("backfill_dryrun.json" if a.dry_run else "backfill_result.json")).write_text(
            json.dumps({"run_at": iso(now), **r, "matches": rep}, ensure_ascii=False, indent=1), encoding="utf-8")
        import collections
        print(json.dumps(r, ensure_ascii=False), collections.Counter(x["action"] for x in rep))


if __name__ == "__main__":
    main()
