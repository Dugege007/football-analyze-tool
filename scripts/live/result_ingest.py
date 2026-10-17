"""赛果定时入库（只写研究副本 v2d3，绝不写正式库）。

节奏按 docs/schema/v2_0-result-ingest-schedule.md（用户 2026-10-07 拍板）：
开赛后 3 小时主拉，之后每 30 分钟补拉一次，开赛后 6 小时截止；截止后仍没有赛果的场次挂日核对队列。

来源：
- 竞彩网赛果（sporttery，getUniformMatchResultV1 的原始分页 JSON）。本机出口通常被拦（HTTP 567），
  所以本程序只尝试直连一次，失败就读本地缓存目录。缓存由大陆本机刷新后放入。大陆本机取数时优先使用 YPC（即 YuePC）。YPC 不在线或取不到时，才使用 MSI。
- 5DollarFootballAPI 的中国体彩清单 /chinasportslottery（自带完赛比分和状态），密钥只从环境变量
  FIVEDOLLAR_FOOTBALL_API_KEY 读取，不打印、不落盘。
- Football-Data.co.uk 公开 CSV（只在两个来源都没有比分时作兜底核对，由 fd_crosscheck 子命令执行）。

写库规则：
- 只插入 results 里缺失的行，不覆盖已有的行。
- 新插入的完场比分提交前，用与 backfill_market_settle 相同的单场重算写入 market_settle。只写当前这个库，不打开双写，不计算竞彩赛果。
- 同一事务里把新完赛场次放进预期进球队列 xg_fetch_queue。tick 在本轮赛果处理之后，如果环境里有 BSD 令牌，就按队列拉取一次；没有令牌则保持未填写。第二次拉取排在三小时之后，仍由这个 tick 带走，不另起守护进程。这个队列与盘口补数无关。
- 两个来源都有比分且不一致：不写，标记 score_conflict。
- 竞彩退款：标记 refund；5DF 推迟、取消、腰斩：标记 postponed、cancelled、abandoned；这些都不进可结算赛果。
- 每次有内容要写之前，先用 sqlite backup 备份到 /workspace/backups/v2d3-before-result-ingest-<时间戳>/app.db。
- 新增两张表（只追加，不改已有表）：result_ingest_log 记录每场赛果的来源，result_ingest_flags 记录特殊场次标记。
- 子命令 sporttery_crosscheck（竞彩网二次核对）只处理已经写入、来源仍是 5df_csl、并且单一来源标记尚未解决的场次。
  默认只列出清单。显式加上 --apply 时，仅在竞彩网比分与已经入库的比分完全一致时，把来源改为 sporttery+5df_csl 并解除该标记。
  赛果表里的比分不做任何修改。写入前先整库备份，并写出可以据此回滚的审计文件。
"""
from __future__ import annotations

import argparse
import datetime as dt
# 跨平台文件锁：Linux 与 macOS 使用 fcntl，Windows 使用 api/app/portable_lock.py 中基于 msvcrt 的实现。
import importlib.util as _pl_util
import pathlib as _pl_pathlib
_pl_spec = _pl_util.spec_from_file_location(
    "_portable_lock", _pl_pathlib.Path(__file__).resolve().parents[2] / "api/app/portable_lock.py")
fcntl = _pl_util.module_from_spec(_pl_spec)
_pl_spec.loader.exec_module(fcntl)
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
PROD_DB = os.environ.get("APP_DB_PATH", "/workspace/match-analysis-api/data/app.db")
# 2026-10-15 研究副本并入正式库：flag 文件或 FORMAL_MERGED=1 → 合并后模式（--db 默认指向正式库，允许写）
FORMAL_MERGED = os.environ.get("FORMAL_MERGED", "").strip() == "1" or os.path.exists(
    os.path.join(os.path.dirname(PROD_DB), "FORMAL_MERGED.flag"))
MAIN_PULL_H, RETRY_MIN, CUTOFF_H = 3, 30, 6
GAP_SEC = 3.0
BACKUP_KEEP = 24

SCHEMA = """
CREATE TABLE IF NOT EXISTS result_ingest_log (
  match_id INTEGER PRIMARY KEY REFERENCES matches(id),
  source TEXT NOT NULL,            -- sporttery | 5df_csl | sporttery+5df_csl | 5df_csl+football_data_co_uk
  sporttery_score TEXT, fivedf_score TEXT, fd_score TEXT,
  detail_json TEXT,
  batch_id INTEGER REFERENCES import_batches(id),
  ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS result_ingest_flags (
  match_id INTEGER NOT NULL REFERENCES matches(id),
  flag TEXT NOT NULL,              -- score_conflict | refund | postponed | cancelled | abandoned | no_result_after_cutoff
                                   -- | single_source（只有 5DF 一个来源，Football-Data.co.uk 也没有）| fd_score_mismatch
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
    if FORMAL_MERGED and real == os.path.realpath(PROD_DB):
        return real  # 合并后模式：正式库就是唯一库
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
    res = {"inserted": 0, "flagged": len(flags), "backup": None, "batch_id": None, "market_settle": None}
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
            inserted_ids = []
            for m, d in writes:
                h, a = d["score"]
                c = con.execute("insert or ignore into results(match_id, home_goals, away_goals, total_goals, wdl) "
                                "values (?, ?, ?, ?, ?)", (m["id"], h, a, h + a, wdl(h, a)))
                if c.rowcount:
                    res["inserted"] += 1
                    inserted_ids.append(m["id"])
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
            if inserted_ids:
                res["market_settle"] = _refresh_market_settle(con, inserted_ids, now)
                res["xg_queue"] = _enqueue_xg_after_results(con, inserted_ids, now)
    finally:
        con.close()
    return res


def _enqueue_xg_after_results(con: sqlite3.Connection, match_ids: list[int], now: dt.datetime) -> dict:
    """新写入的完赛场次进入预期进球队列。这里只入队，不请求 BSD。"""
    api_root = Path(__file__).resolve().parents[2] / "api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    from app.xg_obs import enqueue_finished_matches

    return enqueue_finished_matches(con, match_ids, now)


def pull_xg_after_tick(db: str, now: dt.datetime) -> dict:
    """赛果这一轮结束之后拉取已到期的预期进球。没有令牌时不改队列。"""
    api_root = Path(__file__).resolve().parents[2] / "api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    from app.xg_obs import pull_due_expected_goals

    con = sqlite3.connect(db, timeout=60)
    try:
        report = pull_due_expected_goals(con, now, limit=20, sleep_seconds=0)
        con.commit()
        return report
    finally:
        con.close()


def _s(t):
    return f"{t[0]}:{t[1]}" if t else None


def _refresh_market_settle(con: sqlite3.Connection, match_ids: list[int], now: dt.datetime) -> dict:
    """完场比分已经插入之后，在同一个事务里覆盖重算这些场次的机构赛果。"""
    api_root = Path(__file__).resolve().parents[2] / "api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    from app.market_settle import refresh_after_results_written

    return refresh_after_results_written(con, match_ids, now=iso(now))


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
    try:
        out["xg"] = pull_xg_after_tick(db, now)
    except Exception as exc:
        out["xg"] = {"error": str(exc)}
        log(f"预期进球拉取没有完成：{exc}")
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
             "「冲突」是当天开赛、两个来源比分不一致而没有写入的场数；「特殊」是推迟、取消、腰斩或退款的场数；"
             "「单一来源」是当天开赛、只有 5DF 一个来源且 Football-Data.co.uk 也核对不到的场数（标记 single_source）。", ""]
    lines += ["| 日期 | 补到 | 还缺 | 冲突 | 特殊 | 单一来源 | 竞彩网缓存刷新 | 还缺的场次 |",
              "|---|---|---|---|---|---|---|---|"]
    recs = refresh_records(path.parent)
    for i in range(days):
        D = (now - dt.timedelta(days=i)).date()
        lo, hi = f"{D.isoformat()}T00:00:00+08:00", f"{(D + dt.timedelta(days=1)).isoformat()}T00:00:00+08:00"
        ins = con.execute("select count(*) from result_ingest_log where ingested_at >= ? and ingested_at < ?",
                          (lo, hi)).fetchone()[0] if has_log else 0
        miss = [r for r in con.execute(
            "select m.id, m.jc_id, m.home_team, m.away_team, m.kickoff_at from matches m left join results r on r.match_id = m.id "
            "where r.match_id is null and m.kickoff_at >= ? and m.kickoff_at < ?", (lo, hi))
            if now - dt.datetime.fromisoformat(r[4]) >= dt.timedelta(hours=MAIN_PULL_H)]
        conf = spec = single = 0
        if has_log:
            ids = [r[0] for r in miss]
            fl = con.execute("select f.match_id, f.flag from result_ingest_flags f join matches m on m.id = f.match_id "
                             "left join results r on r.match_id = m.id where r.match_id is null and f.resolved_at is null "
                             "and m.kickoff_at >= ? and m.kickoff_at < ?", (lo, hi)).fetchall()
            conf = len({a for a, f in fl if f == "score_conflict"})
            spec = len({a for a, f in fl if f in ("refund", "postponed", "cancelled", "abandoned")})
            single = con.execute("select count(distinct f.match_id) from result_ingest_flags f join matches m on m.id = f.match_id "
                                 "where f.flag = 'single_source' and f.resolved_at is null and m.kickoff_at >= ? "
                                 "and m.kickoff_at < ?", (lo, hi)).fetchone()[0]
        names = "；".join(f"{r[1]} {r[2]} 对 {r[3]}" for r in miss[:12]) + ("等" if len(miss) > 12 else "")
        day = [r for r in recs if (r.get("at") or "")[:10] == D.isoformat()]
        if not day:
            rf = "没有刷新"
        else:
            okr = [r for r in day if r.get("ok")]
            rf = (f"成功（{okr[-1]['at'][11:16]}，{okr[-1]['matches']} 场）" if okr else "失败") + \
                 (f"，另有 {len(day) - len(okr)} 次失败" if okr and len(day) > len(okr) else "") + \
                 (f"：{day[-1].get('error')}" if not okr else "")
        lines.append(f"| {D.isoformat()} | {ins} | {len(miss)} | {conf} | {spec} | {single} | {rf} | {names or '无'} |")
    if has_log:
        lines += ["", "## 待抽查场次（单一来源、与 Football-Data.co.uk 比分不一致）", "",
                  "以下是还没解决的 single_source 和 fd_score_mismatch 标记，供分析师每日抽查。", "",
                  "| 标记 | 编号 | 竞彩日 | 对阵 | 比分 | 来源 |", "|---|---|---|---|---|---|"]
        rows = con.execute(
            "select f.flag, m.jc_id, m.jingcai_date, m.home_team, m.away_team, r.home_goals, r.away_goals, l.source, f.detail_json "
            "from result_ingest_flags f join matches m on m.id = f.match_id left join results r on r.match_id = m.id "
            "left join result_ingest_log l on l.match_id = m.id where f.resolved_at is null "
            "and f.flag in ('single_source', 'fd_score_mismatch') order by m.kickoff_at desc, m.jc_id").fetchall()
        names = {"single_source": "单一来源", "fd_score_mismatch": "与 Football-Data.co.uk 不一致"}
        srcn = {"5df_csl": "只有 5DF", "sporttery": "只有竞彩网"}
        for fl, jc, jd, h, a, hg, ag, src, det in rows:
            sc = f"{hg}-{ag}" if hg is not None else "无"
            extra = ""
            if fl == "fd_score_mismatch":
                try:
                    extra = "；Football-Data.co.uk：" + json.loads(det or "{}").get("fd", "")
                except Exception:
                    pass
            lines.append(f"| {names[fl]} | {jc} | {jd} | {h} 对 {a} | {sc} | {srcn.get(src, src)}{extra} |")
        if not rows:
            lines.append("| 无 | | | | | |")
    con.close()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def periodic_crosscheck(args, st: State, now: dt.datetime, log):
    """最近 7 天只有 5DF 一个来源的场次：每 12 小时最多刷新一次 Football-Data.co.uk CSV，然后核对并标记。"""
    db = check_db_path(args.db)
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    try:
        n = ro.execute("select count(*) from result_ingest_log where source in ('5df_csl', 'sporttery')").fetchone()[0]
    except sqlite3.Error:
        n = 0
    ro.close()
    if not n:
        return
    since = (now - dt.timedelta(days=7)).date().isoformat()
    last = st.d.get("fd_refreshed_at")
    if not last or now - dt.datetime.fromisoformat(last) >= dt.timedelta(hours=12):
        k = fd_refresh(Path(args.out_dir) / "fd_raw", Path(args.call_log))
        st.d["fd_refreshed_at"] = iso(now)
        st.save()
        log(f"Football-Data.co.uk 公开 CSV 刷新 {k} 个文件")
    r = crosscheck_single_source(db, Path(args.out_dir), Path(args.backup_root), now, since=since)
    if r["backup"]:
        prune_backups(Path(args.backup_root))
        log(f"单一来源核对：检查 {r['checked']} 场，Football-Data.co.uk 证实 {r['confirmed']} 场，"
            f"单一来源 {r['single_source']} 场，比分不一致 {r['fd_score_mismatch']} 场，备份 {r['backup']}")


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
                periodic_crosscheck(args, st, now, log)
            except Exception as e:
                log(f"Football-Data.co.uk 核对出错：{type(e).__name__}: {e}")
            try:
                write_daily_summary(check_db_path(args.db), out_dir / "daily_summary.md", now)
                last_summary = now
            except Exception as e:
                log(f"汇总出错：{e}")
        (out_dir / "heartbeat.json").write_text(json.dumps({"pid": os.getpid(), "at": iso(now_cn()),
                                                            "api_calls_5df": cl.calls}), encoding="utf-8")
        time.sleep(args.interval)


# ----------------------------------------------------------------------------- 竞彩网缓存刷新（大陆本机，YPC 优先，失败再用 MSI）

SPORTTERY_CACHE = "/workspace/odds-data/research/jingcai-competition-whitelist/cache/sporttery"
REFRESH_MAX_AGE_H = 3


def import_sporttery_bundle(bundle: Path | None, cache_dir: Path, out_dir: Path, now: dt.datetime,
                            note: str | None = None, dry_run: bool = False) -> dict:
    """把大陆本机上 refresh_sporttery.ps1 生成的 latest_bundle.json 拆页写进缓存目录，并把这次刷新是否成功
    追加到 out_dir/sporttery_refresh.jsonl（每日汇总读它）。大陆本机取数时优先使用 YPC（即 YuePC）。YPC 不在线或取不到时，才使用 MSI。bundle 缺失、过期或内容不对都记为失败。"""
    rec = {"at": iso(now), "ok": False, "pages": 0, "matches": 0, "range": None, "error": None}
    try:
        if bundle is None or not bundle.exists():
            raise ValueError(note or "没有拿到大陆本机（YPC 优先，失败再用 MSI）的刷新文件。")
        doc = json.loads(bundle.read_text(encoding="utf-8-sig"))
        rec["range"] = f"{doc.get('begin')}..{doc.get('end')}"
        fa = dt.datetime.fromisoformat(doc["fetched_at"])
        if now - fa > dt.timedelta(hours=REFRESH_MAX_AGE_H):
            raise ValueError(f"刷新文件是 {doc['fetched_at']} 生成的，已超过 {REFRESH_MAX_AGE_H} 小时，视为本次没有刷新")
        if not doc.get("ok"):
            raise ValueError(f"大陆本机（YPC 优先，失败再用 MSI）请求竞彩网失败：{doc.get('error')}")
        pages = doc.get("pages") or []
        if isinstance(pages, str):
            pages = [pages]
        if not pages:
            raise ValueError("刷新文件里没有分页")
        n = 0
        if not dry_run:
            cache_dir.mkdir(parents=True, exist_ok=True)
        for i, raw in enumerate(pages, 1):
            j = json.loads(raw)
            if str(j.get("errorCode")) != "0":
                raise ValueError(f"第 {i} 页 errorCode={j.get('errorCode')}")
            n += len((j.get("value") or {}).get("matchResult") or [])
            if dry_run:
                continue
            tmp = cache_dir / f".msi_{doc['begin']}_{doc['end']}_p{i}.tmp"
            tmp.write_text(raw, encoding="utf-8")
            tmp.replace(cache_dir / f"msi_{doc['begin']}_{doc['end']}_p{i}.json")
        rec.update(ok=True, pages=len(pages), matches=n, fetched_at=doc["fetched_at"])
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
    if dry_run:  # 干跑：不写缓存、不记刷新记录
        rec["dry_run"] = True
        return rec
    with (out_dir / "sporttery_refresh.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def refresh_records(out_dir: Path) -> list[dict]:
    p = out_dir / "sporttery_refresh.jsonl"
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


# ----------------------------------------------------------------------------- Football-Data.co.uk 核对

FD_SEASON = "2627"
FD_MAIN = ["E0", "E1", "E2", "E3", "EC", "SC0", "SC1", "SC2", "SC3", "D1", "D2", "I1", "I2", "SP1", "SP2",
           "F1", "F2", "N1", "B1", "P1", "T1", "G1"]
FD_NEW = ["ARG", "AUT", "BRA", "CHN", "DNK", "FIN", "IRL", "JPN", "MEX", "NOR", "POL", "ROU", "RUS", "SWE", "SWZ", "USA"]
TEAMS_CSV = "/workspace/odds-data/research/name-standard-2026-10-14/teams.csv"


def fd_refresh(fd_dir: Path, call_log: Path) -> int:
    """下载 Football-Data.co.uk 公开 CSV（免费，不需要密钥）。返回成功的文件数。"""
    fd_dir.mkdir(parents=True, exist_ok=True)
    urls = [(f"mmz4281_{FD_SEASON}_{c}.csv", f"https://www.football-data.co.uk/mmz4281/{FD_SEASON}/{c}.csv") for c in FD_MAIN]
    urls += [(f"new_{c}.csv", f"https://www.football-data.co.uk/new/{c}.csv") for c in FD_NEW]
    ok = 0
    for name, u in urls:
        code = -1
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=40)
            code, body = r.status, r.read()
            if code == 200 and body[:3] in (b"Div", b"Cou", b"\xef\xbb\xbf"):
                tmp = fd_dir / (name + ".tmp")
                tmp.write_bytes(body)
                tmp.replace(fd_dir / name)
                ok += 1
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:
            code = -1
        with call_log.open("a", encoding="utf-8") as f:
            f.write(f"{iso(now_cn())}\tfootball_data\t{name}\t\t\t{code}\t\t\n")
        time.sleep(1.0)
    return ok


def _norm(n: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (n or "").lower())


def fd_rows(fd_dir: Path) -> list[dict]:
    import csv
    out = []
    for f in sorted(glob.glob(str(fd_dir / "*.csv"))):
        try:
            rd = csv.DictReader(open(f, encoding="utf-8-sig", errors="replace"))
            for r in rd:
                h, a = r.get("HomeTeam") or r.get("Home"), r.get("AwayTeam") or r.get("Away")
                hg, ag = r.get("FTHG") or r.get("HG"), r.get("FTAG") or r.get("AG")
                try:
                    d = dt.datetime.strptime(r["Date"], "%d/%m/%Y" if len(r["Date"]) == 10 else "%d/%m/%y").date()
                except Exception:
                    continue
                if h and a and hg not in (None, "") and ag not in (None, ""):
                    out.append({"date": d, "home": _norm(h), "away": _norm(a), "home_raw": h, "away_raw": a,
                                "score": (int(float(hg)), int(float(ag))), "file": os.path.basename(f)})
        except Exception:
            continue
    return out


def english_names(con: sqlite3.Connection, extra_map: Path | None = None) -> dict[str, set[str]]:
    """中文名 → 英文名集合（规范化后）。来源：定稿 teams.csv 的英文别名、库里 team_aliases 的英文别名、
    out_dir/fd_team_map.csv（中文,英文）手工补充。"""
    import csv
    groups: list[set[str]] = []
    if os.path.exists(TEAMS_CSV):
        for r in csv.DictReader(open(TEAMS_CSV, encoding="utf-8-sig")):
            groups.append({r["canonical"], *[x for x in (r.get("aliases") or "").split("|") if x]})
    try:
        by = {}
        for alias, tid, canon in con.execute("select a.alias, a.team_id, t.name_zh_canonical from team_aliases a "
                                             "join teams t on t.id = a.team_id"):
            by.setdefault(tid, {canon}).add(alias)
        groups += list(by.values())
    except sqlite3.Error:
        pass
    if extra_map and extra_map.exists():
        for r in csv.reader(open(extra_map, encoding="utf-8-sig")):
            if len(r) >= 2 and not r[0].startswith("#") and r[0] != "中文":
                groups.append({r[0], r[1]})
    out: dict[str, set[str]] = {}
    for g in groups:
        en = {_norm(x) for x in g if re.search(r"[A-Za-z]", x) and not re.search(r"[\u4e00-\u9fff]", x)}
        if not en:
            continue
        for x in g:
            if re.search(r"[\u4e00-\u9fff]", x):
                out.setdefault(x, set()).update(en)
    return out


def fd_lookup(rows: list[dict], kickoff_at: str, home_en: set[str], away_en: set[str]):
    """按开赛日（北京时间当天或前一天，Football-Data 用英国当地日期）+ 主客队英文名找一行。
    两队都对上优先；只有一队对上时，要求当天那支队只有一场。"""
    ko = dt.datetime.fromisoformat(kickoff_at).astimezone(TZ).date()
    days = {ko, ko - dt.timedelta(days=1)}
    cand = [r for r in rows if r["date"] in days]
    both = [r for r in cand if r["home"] in home_en and r["away"] in away_en]
    if both:
        return both[0]
    one = [r for r in cand if (home_en and r["home"] in home_en) or (away_en and r["away"] in away_en)]
    return one[0] if len(one) == 1 else None


def crosscheck_single_source(db: str, out_dir: Path, backup_root: Path, now: dt.datetime, *, since: str | None = None,
                             dry_run: bool = False) -> dict:
    """只有一个来源（result_ingest_log.source = '5df_csl' 或 'sporttery'，2026-10-14 分析师规则）的场次：拿 Football-Data.co.uk 再核对一次。
    - 对上且比分一致：source 改为 5df_csl+football_data_co_uk，fd_score 写入，已有的 single_source 标记置为已解决。
    - 对上但比分不一致：标 fd_score_mismatch（赛果保留，等人工抽查）。
    - 对不上：标 single_source。"""
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    q = ("select l.match_id, m.kickoff_at, m.jc_id, m.home_team, m.away_team, coalesce(l.fivedf_score, l.sporttery_score), "
         "l.fd_score, l.detail_json, l.source "
         "from result_ingest_log l join matches m on m.id = l.match_id where l.source in ('5df_csl', 'sporttery')")
    args = []
    if since:
        q += " and m.kickoff_at >= ?"
        args.append(since)
    todo = ro.execute(q, args).fetchall()
    names = english_names(ro, out_dir / "fd_team_map.csv")
    ro.close()
    rows = fd_rows(out_dir / "fd_raw")
    plan = []
    for mid, ko, jc, h, a, fs, fds, det, src in todo:
        r = fd_lookup(rows, ko, names.get(h, set()), names.get(a, set()))
        five = tuple(int(x) for x in fs.split(":")) if fs else None
        if r and five and r["score"] == five:
            plan.append((mid, "confirm", {"fd": f"{r['file']} {r['date']} {r['home_raw']} {r['score'][0]}-{r['score'][1]} {r['away_raw']}",
                                          "source": src}, r["score"]))
        elif r:
            plan.append((mid, "fd_score_mismatch", {"source": src, "score": fs, "fd": f"{r['file']} {r['home_raw']} {r['score'][0]}-{r['score'][1]} {r['away_raw']}"}, r["score"]))
        else:
            plan.append((mid, "single_source", {"source": src, "score": fs, "jc_id": jc, "match": f"{h} 对 {a}",
                                                "fd_crosscheck": "Football-Data.co.uk 公开 CSV 里找不到这场"}, None))
    res = {"checked": len(plan), "confirmed": sum(1 for p in plan if p[1] == "confirm"),
           "single_source": sum(1 for p in plan if p[1] == "single_source"),
           "fd_score_mismatch": sum(1 for p in plan if p[1] == "fd_score_mismatch"), "backup": None,
           "plan": [{"match_id": p[0], "action": p[1], **p[2]} for p in plan]}
    if dry_run or not plan:
        return res
    # 只在会改变库内容时写（已有同样的未解决标记就不重复备份）
    con = sqlite3.connect(db, timeout=60)
    ensure_schema(con)
    existing = {(m, f) for m, f in con.execute("select match_id, flag from result_ingest_flags where resolved_at is null")}
    con.close()
    changing = [p for p in plan if p[1] == "confirm" or (p[0], p[1]) not in existing]
    if not changing:
        return res
    res["backup"] = backup(db, backup_root)
    con = sqlite3.connect(db, timeout=60)
    try:
        with con:
            for mid, act, det, sc in plan:
                if act == "confirm":
                    con.execute("update result_ingest_log set source = ?, fd_score = ?, "
                                "detail_json = json_set(coalesce(detail_json, '{}'), '$.fd_crosscheck', ?) "
                                "where match_id = ? and source = ?",
                                (det["source"] + "+football_data_co_uk", _s(sc), det["fd"], mid, det["source"]))
                    con.execute("update result_ingest_flags set resolved_at = ? where match_id = ? and "
                                "flag = 'single_source' and resolved_at is null", (iso(now), mid))
                else:
                    con.execute("insert into result_ingest_flags(match_id, flag, detail_json, first_seen_at, last_seen_at) "
                                "values (?, ?, ?, ?, ?) on conflict(match_id, flag) do update set "
                                "detail_json = excluded.detail_json, last_seen_at = excluded.last_seen_at",
                                (mid, act, json.dumps(det, ensure_ascii=False), iso(now), iso(now)))
    finally:
        con.close()
    return res


# ----------------------------------------------------------------------------- 竞彩网二次核对

DUAL_SOURCE = "sporttery+5df_csl"
SPORTTERY_CROSSCHECK_AUDIT_KIND = "sporttery_crosscheck_audit"


def _real_name_chars(text: str | None) -> set[str]:
    """只保留汉字和英文字母，避免空格、标点或数字造成队名误匹配。"""
    return set(re.findall(r"[\u4e00-\u9fffA-Za-z]", text or ""))


def _team_side_matches(stored_name: str | None, *cache_names: str | None) -> bool:
    """库里的队名与竞彩网队名至少有一个相同的汉字或英文字母时，认为这一侧队名对上了。"""
    left = _real_name_chars(stored_name)
    right: set[str] = set()
    for name in cache_names:
        right |= _real_name_chars(name)
    return bool(left and right and (left & right))


def _parse_score_text(text: str | None):
    """把已经入库的比分文本解析成整数对。无法解析时返回空。"""
    if text is None:
        return None
    matched = re.fullmatch(r"(\d+):(\d+)", str(text).strip())
    if not matched:
        return None
    return int(matched.group(1)), int(matched.group(2))


def find_sporttery_result(cache: dict, jingcai_date: str | None, jc_id: str | None, home: str | None, away: str | None) -> dict:
    """按竞彩日或开赛次日的短编号查找竞彩网赛果，并且要求主队和客队两侧都能对上队名。

    查找窗口与 sporttery_lookup 相同：竞彩网的 matchDate 是开赛自然日，可能比竞彩日晚一天。
    某一天已经有这个编号、但队名对不上时，不再改用另一天，避免把别的场次当成这一场。
    返回 outcome 为 matched、name_mismatch 或 missing。
    """
    code = short_code(jc_id)
    if not code or not jingcai_date or not home or not away:
        return {"outcome": "missing", "match_result": None}
    try:
        day0 = dt.date.fromisoformat(str(jingcai_date)[:10])
    except ValueError:
        return {"outcome": "missing", "match_result": None}
    for day in (day0, day0 + dt.timedelta(days=1)):
        match_result = cache.get((day.isoformat(), code))
        if match_result is None:
            continue
        home_ok = _team_side_matches(home, match_result.get("allHomeTeam"), match_result.get("homeTeam"))
        away_ok = _team_side_matches(away, match_result.get("allAwayTeam"), match_result.get("awayTeam"))
        if home_ok and away_ok:
            return {"outcome": "matched", "match_result": match_result}
        return {"outcome": "name_mismatch", "match_result": match_result}
    return {"outcome": "missing", "match_result": None}


def _write_json(path: Path, payload: dict) -> None:
    """把字典写成 UTF-8 的 JSON 文件。先写临时文件再替换，避免读到半截内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)


def _load_single_source_rows(db: str) -> list[dict]:
    """读取来源仍是 5df_csl、并且单一来源标记尚未解决的已入库场次。只读，不建表。"""
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    try:
        log_exists = connection.execute(
            "select 1 from sqlite_master where type = 'table' and name = 'result_ingest_log'").fetchone()
        flag_exists = connection.execute(
            "select 1 from sqlite_master where type = 'table' and name = 'result_ingest_flags'").fetchone()
        if not log_exists or not flag_exists:
            return []
        rows = connection.execute(
            "select l.match_id, m.jingcai_date, m.jc_id, m.home_team, m.away_team, m.kickoff_at, "
            "l.source, l.sporttery_score, l.fivedf_score, l.fd_score, l.detail_json, "
            "r.home_goals, r.away_goals "
            "from result_ingest_log l "
            "join matches m on m.id = l.match_id "
            "left join results r on r.match_id = l.match_id "
            "where l.source = '5df_csl' and exists ("
            "select 1 from result_ingest_flags f where f.match_id = l.match_id "
            "and f.flag = 'single_source' and f.resolved_at is null) "
            "order by m.jingcai_date, m.jc_id, l.match_id").fetchall()
    finally:
        connection.close()
    keys = ("match_id", "jingcai_date", "jc_id", "home_team", "away_team", "kickoff_at",
            "source", "sporttery_score", "fivedf_score", "fd_score", "detail_json", "home_goals", "away_goals")
    return [dict(zip(keys, row)) for row in rows]


def _base_crosscheck_item(row: dict) -> dict:
    """整理一场比赛在升级前的字段，供清单和审计文件共用。"""
    stored = None
    if row["home_goals"] is not None and row["away_goals"] is not None:
        stored = (int(row["home_goals"]), int(row["away_goals"]))
    return {
        "match_id": row["match_id"],
        "jc_id": row["jc_id"],
        "jingcai_date": row["jingcai_date"],
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "kickoff_at": row["kickoff_at"],
        "stored_score": _s(stored),
        "results_home_goals": None if stored is None else stored[0],
        "results_away_goals": None if stored is None else stored[1],
        "sporttery_score": None,
        "sporttery_home": None,
        "sporttery_away": None,
        "sporttery_match_date": None,
        "before": {
            "source": row["source"],
            "sporttery_score": row["sporttery_score"],
            "fivedf_score": row["fivedf_score"],
            "fd_score": row["fd_score"],
            "detail_json": row["detail_json"],
            "single_source_resolved_at": None,
        },
        "_stored_pair": stored,
        "_fivedf_pair": _parse_score_text(row["fivedf_score"]),
        "_existing_sporttery_pair": _parse_score_text(row["sporttery_score"]) if row["sporttery_score"] else None,
        "_fd_pair": _parse_score_text(row["fd_score"]) if row["fd_score"] else None,
    }


def classify_sporttery_crosscheck_row(row: dict, cache: dict) -> dict:
    """判定一场比赛是将升级、比分不一致，还是竞彩网没有可用数据。不写数据库。"""
    item = _base_crosscheck_item(row)
    found = find_sporttery_result(cache, row["jingcai_date"], row["jc_id"], row["home_team"], row["away_team"])
    if found["outcome"] == "missing":
        item["category"] = "no_data"
        item["reason"] = "竞彩网缓存里没有与这场编号和两队队名同时对上的赛果。"
        return item
    match_result = found["match_result"] or {}
    item["sporttery_home"] = match_result.get("allHomeTeam") or match_result.get("homeTeam")
    item["sporttery_away"] = match_result.get("allAwayTeam") or match_result.get("awayTeam")
    item["sporttery_match_date"] = (match_result.get("matchDate") or "")[:10] or None
    if found["outcome"] == "name_mismatch":
        item["category"] = "no_data"
        item["reason"] = "竞彩网缓存里同一编号的记录与库里的主队或客队对不上，为避免误匹配，本场不采用。"
        return item
    sporttery_pair = sporttery_score(match_result)
    item["sporttery_score"] = _s(sporttery_pair)
    if match_result.get("poolStatus") == "Refund":
        item["category"] = "mismatch"
        item["reason"] = "竞彩网把这场标为退款。即使比分数字相同，本次也不升级来源，也不修改比分。"
        return item
    stored = item["_stored_pair"]
    if stored is None:
        item["category"] = "mismatch"
        item["reason"] = "赛果表里没有这场的完场比分，无法与竞彩网核对，本次不修改。"
        return item
    if item["_fivedf_pair"] != stored:
        item["category"] = "mismatch"
        item["reason"] = "赛果日志里的五元足球比分与赛果表里的完场比分不一致，本次不修改。"
        return item
    if row["sporttery_score"] and item["_existing_sporttery_pair"] != stored:
        item["category"] = "mismatch"
        item["reason"] = "赛果日志里已经记录了另一条竞彩网比分，与已经入库的比分不一致，本次不修改。"
        return item
    if row["fd_score"] and item["_fd_pair"] != stored:
        item["category"] = "mismatch"
        item["reason"] = "赛果日志里的 Football-Data 比分与已经入库的比分不一致，本次不修改。"
        return item
    if sporttery_pair is None:
        item["category"] = "no_data"
        item["reason"] = "竞彩网缓存里有这场，但是没有可解析的完场比分，本次不修改。"
        return item
    if sporttery_pair != stored:
        item["category"] = "mismatch"
        item["reason"] = (f"竞彩网完场比分是 {_s(sporttery_pair)}，已经入库的比分是 {_s(stored)}，"
                          "两边不一致，本场不修改。")
        return item
    item["category"] = "upgrade"
    item["reason"] = "竞彩网完场比分与已经入库的比分完全一致，来源可以升级为 sporttery+5df_csl，并解除单一来源未解决标记。"
    item["after_source"] = DUAL_SOURCE
    item["after_sporttery_score"] = _s(sporttery_pair)
    return item


def _public_crosscheck_item(item: dict) -> dict:
    """去掉仅供内部比较的字段，使清单和审计文件只保留可读内容。"""
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _group_crosscheck(items: list[dict]) -> dict:
    """把判定结果分成将升级、比分不一致、竞彩网没有数据三类。"""
    public = [_public_crosscheck_item(item) for item in items]
    upgrades = [item for item in public if item["category"] == "upgrade"]
    mismatches = [item for item in public if item["category"] == "mismatch"]
    no_data = [item for item in public if item["category"] == "no_data"]
    return {
        "mode": "crosscheck",
        "checked": len(public),
        "upgrade_count": len(upgrades),
        "mismatch_count": len(mismatches),
        "no_data_count": len(no_data),
        "upgrade_matches": upgrades,
        "mismatch_matches": mismatches,
        "no_data_matches": no_data,
        "applied": False,
        "wrote": False,
        "backup": None,
        "audit": None,
        "report": None,
    }


def _finish_report(result: dict, out_dir: Path, now: dt.datetime, filename: str) -> dict:
    """把本次清单写成 UTF-8 的 JSON 文件，并带上命令行要用的中文句子。"""
    report_path = out_dir / filename
    result["report"] = str(report_path)
    result["lines"] = _crosscheck_lines(result)
    _write_json(report_path, {"run_at": iso(now), **result})
    return result


def _crosscheck_lines(result: dict) -> list[str]:
    """生成命令行上要打印的完整中文句子。"""
    if result.get("mode") == "rollback":
        verb = "已回退" if result.get("wrote") else "将回退"
        lines = [
            f"审计文件里有 {result['checked']} 场升级记录。",
            f"可以回退 {result['restore_count']} 场，不能回退 {result['skip_count']} 场。",
        ]
        if result.get("wrote"):
            lines.append(f"已经按审计文件回退。本次备份目录是 {result['backup']}。回退审计文件是 {result['audit']}。")
        else:
            lines.append("本次没有修改数据库。")
        for item in result["restore_matches"]:
            lines.append(
                f"{verb}：比赛 {item['match_id']}，编号 {item.get('jc_id')}，"
                f"{item.get('home_team')} 对 {item.get('away_team')}。"
                f"来源从 {item['after']['source']} 回到 {item['before']['source']}，"
                f"竞彩网比分字段从 {item['after']['sporttery_score']} 回到 {item['before']['sporttery_score']}，"
                "单一来源标记恢复为未解决。")
        for item in result["skip_matches"]:
            lines.append(f"不能回退：比赛 {item.get('match_id')}。{item.get('reason')}")
        return lines
    verb = "已升级" if result.get("wrote") else "将升级"
    lines = [
        f"本次检查了 {result['checked']} 场来源仍为 5df_csl 且单一来源标记尚未解决的比赛。",
        f"{verb} {result['upgrade_count']} 场，比分不一致 {result['mismatch_count']} 场，"
        f"竞彩网没有数据 {result['no_data_count']} 场。",
    ]
    if result.get("wrote"):
        lines.append(f"已经写入数据库。备份目录是 {result['backup']}。审计文件是 {result['audit']}。")
    else:
        lines.append("本次没有修改数据库。")
    for item in result["upgrade_matches"]:
        lines.append(
            f"{verb}：比赛 {item['match_id']}，编号 {item['jc_id']}，竞彩日 {item['jingcai_date']}，"
            f"{item['home_team']} 对 {item['away_team']}，比分 {item['stored_score']}。{item['reason']}")
    for item in result["mismatch_matches"]:
        lines.append(
            f"比分不一致：比赛 {item['match_id']}，编号 {item['jc_id']}，竞彩日 {item['jingcai_date']}，"
            f"{item['home_team']} 对 {item['away_team']}。{item['reason']}")
    for item in result["no_data_matches"]:
        lines.append(
            f"竞彩网没有数据：比赛 {item['match_id']}，编号 {item['jc_id']}，竞彩日 {item['jingcai_date']}，"
            f"{item['home_team']} 对 {item['away_team']}。{item['reason']}")
    return lines


def _still_matches_upgrade(connection: sqlite3.Connection, item: dict) -> bool:
    """写入前再读一次当前行，确认它仍然是计划里的那一场，避免覆盖中途被改过的数据。"""
    current = connection.execute(
        "select l.source, l.sporttery_score, l.fivedf_score, l.fd_score, l.detail_json, "
        "r.home_goals, r.away_goals, f.resolved_at "
        "from result_ingest_log l "
        "left join results r on r.match_id = l.match_id "
        "left join result_ingest_flags f on f.match_id = l.match_id and f.flag = 'single_source' "
        "where l.match_id = ?", (item["match_id"],)).fetchone()
    if current is None:
        return False
    source, sporttery_text, fivedf_text, fd_text, detail_json, home_goals, away_goals, resolved_at = current
    before = item["before"]
    return (source == before["source"] == "5df_csl"
            and sporttery_text == before["sporttery_score"]
            and fivedf_text == before["fivedf_score"]
            and fd_text == before["fd_score"]
            and detail_json == before["detail_json"]
            and resolved_at is None
            and home_goals == item["results_home_goals"]
            and away_goals == item["results_away_goals"])


def run_sporttery_crosscheck(db: str, sporttery_dirs: list[str], backup_root: Path, out_dir: Path,
                             now: dt.datetime, *, apply: bool) -> dict:
    """执行竞彩网二次核对。apply 为假时只列出清单；为真时只升级比分完全一致的场次。

    升级只修改赛果日志的来源和竞彩网比分字段，并给单一来源标记写上解决时间。
    不修改赛果表里的进球数。没有任何场次需要升级时，不制作备份。
    """
    db = check_db_path(db)
    cache = load_sporttery(sporttery_dirs)
    classified = [classify_sporttery_crosscheck_row(row, cache) for row in _load_single_source_rows(db)]
    result = _group_crosscheck(classified)
    result["applied"] = bool(apply)
    if not apply or result["upgrade_count"] == 0:
        report_name = "sporttery_crosscheck_dryrun.json" if not apply else "sporttery_crosscheck_result.json"
        return _finish_report(result, out_dir, now, report_name)
    backup_dir = backup(db, backup_root, "sporttery-crosscheck")
    result["backup"] = backup_dir
    resolved_at = iso(now)
    audit_upgrades = []
    connection = sqlite3.connect(db, timeout=60)
    try:
        with connection:
            for item in result["upgrade_matches"]:
                if not _still_matches_upgrade(connection, item):
                    raise RuntimeError(
                        f"比赛 {item['match_id']} 在备份之后已经变化，本次写入已全部取消。请重新预览后再决定是否写入。")
                updated = connection.execute(
                    "update result_ingest_log set source = ?, sporttery_score = ? "
                    "where match_id = ? and source = '5df_csl'",
                    (DUAL_SOURCE, item["after_sporttery_score"], item["match_id"]))
                if updated.rowcount != 1:
                    raise RuntimeError(f"比赛 {item['match_id']} 的来源没有更新成功，本次写入已全部取消。")
                flagged = connection.execute(
                    "update result_ingest_flags set resolved_at = ? "
                    "where match_id = ? and flag = 'single_source' and resolved_at is null",
                    (resolved_at, item["match_id"]))
                if flagged.rowcount != 1:
                    raise RuntimeError(f"比赛 {item['match_id']} 的单一来源标记没有解除，本次写入已全部取消。")
                audit_upgrades.append({
                    "match_id": item["match_id"],
                    "jc_id": item["jc_id"],
                    "jingcai_date": item["jingcai_date"],
                    "home_team": item["home_team"],
                    "away_team": item["away_team"],
                    "results_home_goals": item["results_home_goals"],
                    "results_away_goals": item["results_away_goals"],
                    "sporttery_match_date": item["sporttery_match_date"],
                    "sporttery_home": item["sporttery_home"],
                    "sporttery_away": item["sporttery_away"],
                    "before": item["before"],
                    "after": {
                        "source": DUAL_SOURCE,
                        "sporttery_score": item["after_sporttery_score"],
                        "fivedf_score": item["before"]["fivedf_score"],
                        "fd_score": item["before"]["fd_score"],
                        "detail_json": item["before"]["detail_json"],
                        "single_source_resolved_at": resolved_at,
                    },
                })
    except Exception:
        connection.close()
        raise
    connection.close()
    result["wrote"] = True
    stamp = Path(backup_dir).name.rsplit("-", 1)[-1]
    audit = {
        "kind": SPORTTERY_CROSSCHECK_AUDIT_KIND,
        "run_at": iso(now),
        "backup": backup_dir,
        "upgrades": audit_upgrades,
    }
    audit_path = out_dir / f"sporttery_crosscheck_audit_{stamp}.json"
    _write_json(Path(backup_dir) / "sporttery_crosscheck_audit.json", audit)
    _write_json(audit_path, audit)
    result["audit"] = str(audit_path)
    return _finish_report(result, out_dir, now, "sporttery_crosscheck_result.json")


def _rollback_plan(db: str, audit_path: Path) -> dict:
    """对照审计文件和当前数据库，分出仍可回退的场次和必须跳过的场次。只读。"""
    try:
        document = json.loads(audit_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SystemExit(f"找不到审计文件：{audit_path}") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"审计文件不是合法的 JSON：{audit_path}") from error
    if not isinstance(document, dict) or document.get("kind") != SPORTTERY_CROSSCHECK_AUDIT_KIND:
        raise SystemExit("这个文件不是竞彩网二次核对的审计文件，不能用来回退。")
    upgrades = document.get("upgrades") or []
    if not isinstance(upgrades, list):
        raise SystemExit("审计文件里的升级记录格式不正确，不能用来回退。")
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    restore_matches = []
    skip_matches = []
    try:
        for item in upgrades:
            if not isinstance(item, dict) or "match_id" not in item or "before" not in item or "after" not in item:
                skip_matches.append({"match_id": None, "reason": "审计文件里有一条记录缺少升级前后的字段，本条不回退。"})
                continue
            current = connection.execute(
                "select l.source, l.sporttery_score, l.fivedf_score, l.fd_score, l.detail_json, "
                "r.home_goals, r.away_goals, f.resolved_at "
                "from result_ingest_log l "
                "left join results r on r.match_id = l.match_id "
                "left join result_ingest_flags f on f.match_id = l.match_id and f.flag = 'single_source' "
                "where l.match_id = ?", (item["match_id"],)).fetchone()
            reason = _rollback_skip_reason(item, current)
            if reason:
                skip_matches.append({"match_id": item["match_id"], "jc_id": item.get("jc_id"), "reason": reason})
                continue
            restore_matches.append(item)
    finally:
        connection.close()
    return {
        "mode": "rollback",
        "checked": len(upgrades),
        "restore_count": len(restore_matches),
        "skip_count": len(skip_matches),
        "restore_matches": restore_matches,
        "skip_matches": skip_matches,
        "applied": False,
        "wrote": False,
        "backup": None,
        "audit": None,
        "source_audit": str(audit_path),
        "report": None,
    }


def _same_value(left, right) -> bool:
    """比较审计值和数据库值。两者都是空时视为相同。"""
    return left == right


def _rollback_skip_reason(item: dict, current) -> str | None:
    """当前行若已不再是审计里的升级后状态，返回不能回退的原因；可以回退时返回空。"""
    if current is None:
        return "数据库里已经找不到这场的赛果日志，本场不回退。"
    source, sporttery_text, fivedf_text, fd_text, detail_json, home_goals, away_goals, resolved_at = current
    after = item["after"]
    if not _same_value(source, after.get("source")) or not _same_value(sporttery_text, after.get("sporttery_score")):
        return "当前来源或竞彩网比分字段已经不是审计文件中的升级后数值，为避免覆盖后来的修改，本场不回退。"
    if not _same_value(fivedf_text, after.get("fivedf_score")) or not _same_value(fd_text, after.get("fd_score")):
        return "五元足球比分或 Football-Data 比分已经与审计文件不一致，本场不回退。"
    if not _same_value(detail_json, after.get("detail_json")):
        return "赛果日志的明细已经与审计文件不一致，本场不回退。"
    if home_goals != item.get("results_home_goals") or away_goals != item.get("results_away_goals"):
        return "赛果表里的比分已经与审计时不同，本场不回退。"
    if not _same_value(resolved_at, after.get("single_source_resolved_at")):
        return "单一来源标记的解决时间已经与审计文件不一致，本场不回退。"
    return None


def rollback_sporttery_crosscheck(db: str, audit_path: Path, backup_root: Path, out_dir: Path,
                                  now: dt.datetime, *, apply: bool) -> dict:
    """按审计文件把来源和单一来源标记恢复到升级前。apply 为假时只列出将回退的场次。

    只有当前数值仍与审计中的升级后数值完全一致时才回退。写入前会再做一次整库备份。
    """
    db = check_db_path(db)
    result = _rollback_plan(db, audit_path)
    result["applied"] = bool(apply)
    if not apply or result["restore_count"] == 0:
        report_name = "sporttery_crosscheck_rollback_dryrun.json" if not apply else "sporttery_crosscheck_rollback_result.json"
        return _finish_report(result, out_dir, now, report_name)
    backup_dir = backup(db, backup_root, "sporttery-crosscheck-rollback")
    result["backup"] = backup_dir
    connection = sqlite3.connect(db, timeout=60)
    try:
        with connection:
            for item in result["restore_matches"]:
                current = connection.execute(
                    "select l.source, l.sporttery_score, l.fivedf_score, l.fd_score, l.detail_json, "
                    "r.home_goals, r.away_goals, f.resolved_at "
                    "from result_ingest_log l "
                    "left join results r on r.match_id = l.match_id "
                    "left join result_ingest_flags f on f.match_id = l.match_id and f.flag = 'single_source' "
                    "where l.match_id = ?", (item["match_id"],)).fetchone()
                reason = _rollback_skip_reason(item, current)
                if reason:
                    raise RuntimeError(f"比赛 {item['match_id']} {reason}本次回退已全部取消。")
                before = item["before"]
                after = item["after"]
                updated = connection.execute(
                    "update result_ingest_log set source = ?, sporttery_score = ? where match_id = ? and source = ?",
                    (before["source"], before["sporttery_score"], item["match_id"], after["source"]))
                if updated.rowcount != 1:
                    raise RuntimeError(f"比赛 {item['match_id']} 的来源没有回退成功，本次回退已全部取消。")
                flagged = connection.execute(
                    "update result_ingest_flags set resolved_at = ? "
                    "where match_id = ? and flag = 'single_source' and resolved_at = ?",
                    (before["single_source_resolved_at"], item["match_id"], after["single_source_resolved_at"]))
                if flagged.rowcount != 1:
                    raise RuntimeError(f"比赛 {item['match_id']} 的单一来源标记没有回退成功，本次回退已全部取消。")
    except Exception:
        connection.close()
        raise
    connection.close()
    result["wrote"] = True
    stamp = Path(backup_dir).name.rsplit("-", 1)[-1]
    rollback_audit = {
        "kind": "sporttery_crosscheck_rollback_audit",
        "run_at": iso(now),
        "source_audit": str(audit_path),
        "backup": backup_dir,
        "restored": result["restore_matches"],
    }
    audit_path_out = out_dir / f"sporttery_crosscheck_rollback_{stamp}.json"
    _write_json(Path(backup_dir) / "sporttery_crosscheck_rollback_audit.json", rollback_audit)
    _write_json(audit_path_out, rollback_audit)
    result["audit"] = str(audit_path_out)
    return _finish_report(result, out_dir, now, "sporttery_crosscheck_rollback_result.json")


def main(argv=None):
    p = argparse.ArgumentParser(
        description="赛果定时入库（只写研究副本 v2d3）。子命令 sporttery_crosscheck 是竞彩网二次核对。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "子命令 sporttery_crosscheck 用来做竞彩网二次核对。\n"
            "它只处理来源仍是 5df_csl、并且单一来源标记尚未解决的已入库场次。\n"
            "默认只列出将升级的场次、比分不一致的场次，以及竞彩网缓存里没有数据的场次，不修改数据库。\n"
            "加上 --apply 之后，才会在整库备份之后，把比分完全一致的场次来源改为 sporttery+5df_csl，并解除单一来源未解决标记。赛果表里的比分不会改写。\n"
            "预览回退：python scripts/live/result_ingest.py sporttery_crosscheck --rollback 审计文件.json\n"
            "确认回退：python scripts/live/result_ingest.py sporttery_crosscheck --rollback 审计文件.json --apply\n"
            "回退前会再做一次整库备份。只有当前数值仍与审计文件中的升级后数值一致时，该场才会回到升级前。"
        ),
    )
    p.add_argument("cmd", choices=["daemon", "once", "backfill", "summary", "sporttery_import", "crosscheck",
                                   "sporttery_crosscheck"])
    p.add_argument("--bundle", default=None, help="sporttery_import：大陆本机（YPC 优先，失败再用 MSI）刷新得到的 latest_bundle.json 在本机上的路径")
    p.add_argument("--note", default=None, help="sporttery_import：没拿到文件时写进汇总的原因")
    p.add_argument("--apply", action="store_true",
                   help="只用于竞彩网二次核对：确认写入数据库。不带这个参数时只列出清单，不修改数据库。")
    p.add_argument("--rollback", default=None,
                   help="只用于竞彩网二次核对：审计文件路径。按其中的升级前数值回退来源和单一来源标记。不带 --apply 时只列出将回退的场次。")
    p.add_argument("--sporttery-cache", default=SPORTTERY_CACHE)
    p.add_argument("--fd-refresh", action="store_true", help="crosscheck：先下载 Football-Data.co.uk 公开 CSV")
    p.add_argument("--db", default=os.environ.get("V2D3_DB_PATH") or (PROD_DB if FORMAL_MERGED else "/workspace/match-analysis-api/data/v2d3/app.db"))
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
    if (a.apply or a.rollback) and a.cmd != "sporttery_crosscheck":
        raise SystemExit("--apply 和 --rollback 只用于子命令 sporttery_crosscheck（竞彩网二次核对）。")
    if a.cmd == "sporttery_crosscheck" and a.apply and a.dry_run:
        raise SystemExit("竞彩网二次核对不能同时指定 --apply 和 --dry-run。不带 --apply 时已经是只读预览。")
    od = Path(a.out_dir)
    a.csl_dir = a.csl_dir or str(od / "csl_raw")
    a.sporttery_dir = a.sporttery_dir or str(od / "sporttery_raw")
    a.call_log = a.call_log or str(od / "call_log.tsv")
    if a.sporttery_cache and a.sporttery_cache not in a.extra_sporttery_dir:
        a.extra_sporttery_dir.append(a.sporttery_cache)  # 大陆本机（YPC 优先，失败再用 MSI）刷新的竞彩网缓存
    db = check_db_path(a.db)
    if a.cmd == "daemon":
        daemon(a)
    elif a.cmd == "sporttery_import":
        now = now_cn()
        rec = import_sporttery_bundle(Path(a.bundle) if a.bundle else None, Path(a.sporttery_cache), od, now, a.note,
                                      dry_run=a.dry_run)
        if not a.dry_run:
            write_daily_summary(db, od / "daily_summary.md", now)
        print(json.dumps(rec, ensure_ascii=False))
        if not rec["ok"]:
            sys.exit(2)
    elif a.cmd == "crosscheck":
        now = now_cn()
        if a.fd_refresh:
            print("Football-Data.co.uk 文件数", fd_refresh(od / "fd_raw", Path(a.call_log)))
        r = crosscheck_single_source(db, od, Path(a.backup_root), now, since=a.since, dry_run=a.dry_run)
        (od / ("crosscheck_dryrun.json" if a.dry_run else "crosscheck_result.json")).write_text(
            json.dumps({"run_at": iso(now), **r}, ensure_ascii=False, indent=1), encoding="utf-8")
        write_daily_summary(db, od / "daily_summary.md", now) if not a.dry_run else None
        print(json.dumps({k: v for k, v in r.items() if k != "plan"}, ensure_ascii=False))
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
    elif a.cmd == "sporttery_crosscheck":
        # 竞彩网二次核对：默认只列出清单。--apply 才在备份之后升级来源。--rollback 按审计文件回退。
        now = now_cn()
        if a.rollback:
            result = rollback_sporttery_crosscheck(db, Path(a.rollback), Path(a.backup_root), od, now, apply=a.apply)
        else:
            result = run_sporttery_crosscheck(
                db, [a.sporttery_dir] + list(a.extra_sporttery_dir), Path(a.backup_root), od, now, apply=a.apply)
        for line in result["lines"]:
            print(line)
        summary = {key: value for key, value in result.items()
                   if key not in ("upgrade_matches", "mismatch_matches", "no_data_matches",
                                  "restore_matches", "skip_matches", "lines")}
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
