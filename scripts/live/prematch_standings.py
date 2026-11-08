#!/usr/bin/env python3
"""赛前积分榜快照。

由 scripts/live/live_capture.py 的 tick 在本轮赔率处理之后调用。
按联赛请求 5DollarFootballAPI 的 GET /v1/standings?league=，
把开赛前拉到的名次写入 stats.rank_home、stats.rank_away、source_rank、as_of_rank，
并在 stats_obs（kind=rank）留下同一时刻的观测。

这些接口没有「截至开赛当时」的日期参数，因此只接受拉取时刻早于该场开赛时刻的积分榜。
已经开赛或已经完赛的场次保持原样，不用今天的积分榜回填空的名次列。
国家队等没有积分榜的赛事，若接口返回 standings_not_available，名次留空并标为不可得，
不编造国际足联排名。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

TZ = timezone(timedelta(hours=8))
SOURCE_RANK = "5df_standings"
REASON_NO_STANDINGS = "no_standings"
KIND_RANK = "rank"
# 同一联赛在这个时间窗内不重复请求。窗内新增的未开赛场次仍用上一次拉取结果。
REFRESH = timedelta(minutes=60)
UNAVAILABLE_TOKEN = "standings_not_available"

Clock = Callable[[], datetime]


def iso(dt: datetime) -> str:
    """带时区的北京时间。拒绝没有时区的时刻，避免把朴素时间当成赛前快照。"""
    if dt.tzinfo is None:
        raise ValueError("积分榜快照时刻必须带时区")
    return dt.astimezone(TZ).replace(microsecond=0).isoformat()


def parse_dt(value: Any) -> datetime | None:
    """解析带时区的时刻。没有时区或无法解析时返回空，调用方据此拒绝写入。"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(TZ).replace(microsecond=0)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _norm_name(name: Any) -> str:
    if not isinstance(name, str):
        return ""
    return " ".join(name.strip().casefold().split())


def _walk_strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _walk_strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_strings(value)


def classify_standings(code: int, body: Any) -> tuple[str, dict | None]:
    """把积分榜响应分成 table、unavailable、error。

    只有明确的 standings_not_available 才是「这项赛事没有积分榜」。
    空表、5xx、网络失败都不是不可得，避免一次故障把名次冻成无法获取。
    """
    if not isinstance(body, dict):
        return "error", None
    if any(UNAVAILABLE_TOKEN in text for text in _walk_strings(body)):
        return "unavailable", None
    if code != 200:
        return "error", None
    data = body.get("data")
    if isinstance(data, dict) and isinstance(data.get("table"), list):
        return "table", data
    return "error", None


def index_table(data: dict) -> dict[str, Any]:
    """按球队编号和队名索引积分榜。名次必须是大于等于 1 的整数。"""
    by_id: dict[int, dict] = {}
    by_name: dict[str, list[dict]] = {}
    for row in data.get("table") or []:
        if not isinstance(row, dict):
            continue
        position = _as_int(row.get("position"))
        if position is None or position < 1:
            continue
        team = row.get("team") if isinstance(row.get("team"), dict) else {}
        entry = {
            "position": position,
            "team_id": _as_int(team.get("id")),
            "name": team.get("name") if isinstance(team.get("name"), str) else "",
            "played": row.get("played"),
            "win": row.get("win"),
            "draw": row.get("draw"),
            "lose": row.get("lose"),
            "goals_for": row.get("goals_for"),
            "goals_against": row.get("goals_against"),
            "points": row.get("points"),
        }
        if entry["team_id"] is not None:
            by_id[entry["team_id"]] = entry
        key = _norm_name(entry["name"])
        if key:
            by_name.setdefault(key, []).append(entry)
    return {
        "by_id": by_id,
        "by_name": by_name,
        "season": data.get("season") if isinstance(data.get("season"), str) else None,
        "league_id": _as_int(data.get("league_id")),
    }


def lookup_team(index: dict, team_id: Any, name: Any) -> dict | None:
    """优先用球队编号。编号对不上时，仅在队名唯一命中时才用队名。"""
    tid = _as_int(team_id)
    if tid is not None and tid in index["by_id"]:
        return index["by_id"][tid]
    hits = index["by_name"].get(_norm_name(name)) or []
    if len(hits) == 1:
        return hits[0]
    return None


class RankState:
    """一场比赛当前的名次投影，外加最新 rank 观测的状态。"""

    def __init__(
        self,
        rank_home: int | None = None,
        rank_away: int | None = None,
        source_rank: str | None = None,
        as_of_rank: datetime | None = None,
        status: str | None = None,
    ):
        self.rank_home = rank_home
        self.rank_away = rank_away
        self.source_rank = source_rank
        self.as_of_rank = as_of_rank
        self.status = status

    @property
    def empty(self) -> bool:
        return (
            self.rank_home is None
            and self.rank_away is None
            and not self.source_rank
            and self.as_of_rank is None
        )


def _positive(value: int | None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def is_valid_prematch(state: RankState, kickoff: datetime | None) -> bool:
    """赛前有效快照：截止时刻早于开赛，且两队都有名次，或已明确标记没有积分榜。"""
    if kickoff is None or state.as_of_rank is None or state.as_of_rank >= kickoff:
        return False
    if (
        state.status == "unavailable"
        and state.source_rank == SOURCE_RANK
        and state.rank_home is None
        and state.rank_away is None
    ):
        return True
    return _positive(state.rank_home) and _positive(state.rank_away)


def decide(state: RankState, kickoff: datetime | None, fetched_at: datetime, now: datetime) -> str:
    """决定是否把这一次拉取写进这场比赛。

    返回 write、refresh 或 skip。
    开赛时刻及之后一律 skip。拉取时刻不早于开赛也 skip。
    已有赛前有效快照时，只有本采集源且这次更晚、仍在开赛前，才 refresh。
    其他来源已经写下的名次不覆盖。没有名次、且这场还没开赛时，才 write。
    """
    if kickoff is None or now >= kickoff or fetched_at >= kickoff:
        return "skip"
    if (
        state.source_rank == SOURCE_RANK
        and state.as_of_rank is not None
        and fetched_at <= state.as_of_rank
    ):
        return "skip"
    if is_valid_prematch(state, kickoff):
        if state.source_rank == SOURCE_RANK and fetched_at > state.as_of_rank:
            return "refresh"
        return "skip"
    if state.source_rank not in (None, "", SOURCE_RANK) and (
        state.rank_home is not None or state.rank_away is not None
    ):
        return "skip"
    if state.source_rank == SOURCE_RANK and state.as_of_rank is not None:
        return "refresh"
    return "write"


def _db_writable(db: Path) -> tuple[bool, str]:
    """与 live_capture.ingest 同一条库路径闸门：只写 v2d3 研究副本。

    正式库与研究副本合并之后，采集目标就是那一份库，此函数沿用同一判断，
    不在这里另写一份生产库。库文件不存在时不创建。
    """
    import live_capture as lc

    resolved = db.resolve()
    formal_ok = bool(lc._FORMAL_MERGED and resolved == lc.PROD_DB)
    replica_ok = resolved.name == "app.db" and resolved.parent.name == "v2d3"
    if resolved == lc.PROD_DB and not formal_ok:
        return False, "db_refused"
    if not formal_ok and not replica_ok:
        return False, "db_refused"
    if not resolved.is_file():
        return False, "db_missing"
    return True, "ok"


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_rank_schema(conn: sqlite3.Connection) -> bool:
    """补上已有方案里的名次出处列和 stats_obs。不新建 stats，也不改已有名次的含义。"""
    names = _table_names(conn)
    if "matches" not in names or "stats" not in names:
        return False
    cols = _columns(conn, "stats")
    if "rank_home" not in cols or "rank_away" not in cols:
        return False
    if "source_rank" not in cols:
        conn.execute("ALTER TABLE stats ADD COLUMN source_rank TEXT")
    if "as_of_rank" not in cols:
        conn.execute("ALTER TABLE stats ADD COLUMN as_of_rank TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stats_obs (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id      INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
          kind          TEXT NOT NULL,
          as_of         TEXT NOT NULL,
          source        TEXT NOT NULL,
          payload_json  TEXT NOT NULL,
          provider_ref  TEXT,
          fetched_at    TEXT,
          quality       TEXT,
          extras_json   TEXT,
          UNIQUE (match_id, kind, as_of, source)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_stats_obs_match_kind_asof ON stats_obs (match_id, kind, as_of)"
    )
    return True


def _load_state(conn: sqlite3.Connection, match_id: int) -> RankState:
    row = conn.execute(
        "SELECT rank_home, rank_away, source_rank, as_of_rank FROM stats WHERE match_id=?",
        (match_id,),
    ).fetchone()
    state = RankState()
    if row is not None:
        state.rank_home = row["rank_home"]
        state.rank_away = row["rank_away"]
        state.source_rank = row["source_rank"]
        state.as_of_rank = parse_dt(row["as_of_rank"])
    if "stats_obs" in _table_names(conn):
        obs = conn.execute(
            "SELECT payload_json FROM stats_obs WHERE match_id=? AND kind=? AND source=? "
            "ORDER BY as_of DESC LIMIT 1",
            (match_id, KIND_RANK, SOURCE_RANK),
        ).fetchone()
        if obs is not None:
            try:
                payload = json.loads(obs["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict) and isinstance(payload.get("status"), str):
                state.status = payload["status"]
    return state


def _kickoff_of_row(conn: sqlite3.Connection, match_uid: str) -> tuple[int | None, datetime | None]:
    cols = _columns(conn, "matches")
    kick_sql = "kickoff_at" if "kickoff_at" in cols else "NULL"
    row = conn.execute(
        f"SELECT id, {kick_sql} AS kickoff_at FROM matches WHERE match_uid=?",
        (match_uid,),
    ).fetchone()
    if row is None:
        return None, None
    return int(row["id"]), parse_dt(row["kickoff_at"])


def _assignment_for_match(kind: str, index: dict | None, match: dict, fetched_at: str) -> dict | None:
    """生成一场的写入载荷。两队都对不上积分榜时返回空，不把空名次写成已采集。"""
    league_id = _as_int(match.get("league_id"))
    base = {
        "league_id": league_id,
        "season": None if index is None else index.get("season"),
        "table_as_of": fetched_at,
    }
    if kind == "unavailable":
        return {
            **base,
            "home": None,
            "away": None,
            "status": "unavailable",
            "reason": REASON_NO_STANDINGS,
            "quality": "unavailable",
        }
    if index is None:
        return None
    home = lookup_team(index, match.get("home_id"), match.get("home"))
    away = lookup_team(index, match.get("away_id"), match.get("away"))
    if home is None and away is None:
        return None
    status = "ok" if home is not None and away is not None else "partial"
    return {
        **base,
        "home": None if home is None else home["position"],
        "away": None if away is None else away["position"],
        "status": status,
        "reason": None,
        "quality": status,
        "home_row": home,
        "away_row": away,
    }


def _apply_one(
    conn: sqlite3.Connection,
    match_id: int,
    state: RankState,
    action: str,
    assignment: dict,
    fetched_at: str,
) -> None:
    before = {
        "rank_home": state.rank_home,
        "rank_away": state.rank_away,
        "source_rank": state.source_rank,
        "as_of_rank": iso(state.as_of_rank) if state.as_of_rank else None,
    }
    conn.execute("INSERT INTO stats (match_id) VALUES (?) ON CONFLICT(match_id) DO NOTHING", (match_id,))
    conn.execute(
        "UPDATE stats SET rank_home=?, rank_away=?, source_rank=?, as_of_rank=? WHERE match_id=?",
        (assignment["home"], assignment["away"], SOURCE_RANK, fetched_at, match_id),
    )
    payload = {k: v for k, v in assignment.items() if k != "quality"}
    conn.execute(
        "INSERT INTO stats_obs (match_id, kind, as_of, source, payload_json, provider_ref, fetched_at, quality) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(match_id, kind, as_of, source) DO UPDATE SET "
        "payload_json=excluded.payload_json, provider_ref=excluded.provider_ref, "
        "fetched_at=excluded.fetched_at, quality=excluded.quality",
        (
            match_id,
            KIND_RANK,
            fetched_at,
            SOURCE_RANK,
            json.dumps(payload, ensure_ascii=False),
            str(assignment["league_id"]) if assignment.get("league_id") is not None else None,
            fetched_at,
            assignment["quality"],
        ),
    )
    assignment["_before"] = before
    assignment["_action"] = action


def _read_cache(path: Path) -> dict | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _write_cache(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cache_fresh(cache: dict | None, now: datetime) -> bool:
    if not cache or cache.get("kind") not in ("table", "unavailable"):
        return False
    fetched = parse_dt(cache.get("fetched_at"))
    return fetched is not None and now - fetched < REFRESH


def _upcoming(plans: list[dict], now: datetime) -> list[dict]:
    found = []
    seen = set()
    for plan in plans:
        for match in plan.get("matches") or []:
            uid = match.get("match_uid")
            kick = parse_dt(match.get("kickoff_at"))
            if not uid or kick is None or kick <= now or uid in seen:
                continue
            seen.add(uid)
            found.append(match)
    return found


def _summary(**kwargs) -> dict:
    base = {
        "status": "ok",
        "reason": None,
        "eligible": 0,
        "skipped_no_league": 0,
        "leagues_fetched": 0,
        "leagues_cached": 0,
        "written": 0,
        "refreshed": 0,
        "unavailable": 0,
        "skipped_frozen": 0,
        "skipped_after_kickoff": 0,
        "unmatched": 0,
        "no_replica_match": 0,
        "errors": 0,
    }
    base.update(kwargs)
    return base


def capture_for_plans(
    cl,
    plans: list[dict],
    db: Path,
    *,
    now: datetime | None = None,
    clock: Clock | None = None,
    cache_dir: Path | None = None,
) -> dict:
    """对计划里尚未开赛的场次抓取积分榜并写入允许的库。

    同一联赛只请求一次。不调用任何「按历史日期回放积分榜」的逻辑，因为上游没有这个参数。
    """
    clock = clock or (lambda: datetime.now(TZ).replace(microsecond=0))
    moment = now or clock()
    if moment.tzinfo is None:
        return _summary(status="fail", reason="naive_clock")
    moment = moment.astimezone(TZ).replace(microsecond=0)

    if cache_dir is None:
        import live_capture as lc

        cache_dir = lc.LIVE / "standings"

    upcoming = _upcoming(plans, moment)
    eligible = []
    skipped_no_league = 0
    for match in upcoming:
        if _as_int(match.get("league_id")) is None:
            skipped_no_league += 1
            continue
        eligible.append(match)
    if not eligible:
        return _summary(status="ok", eligible=0, skipped_no_league=skipped_no_league)

    allowed, why = _db_writable(Path(db))
    if not allowed:
        return _summary(status="skipped", reason=why, eligible=len(eligible), skipped_no_league=skipped_no_league)

    conn = sqlite3.connect(str(Path(db).resolve()))
    conn.row_factory = sqlite3.Row
    try:
        if not ensure_rank_schema(conn):
            return _summary(
                status="skipped",
                reason="schema_missing",
                eligible=len(eligible),
                skipped_no_league=skipped_no_league,
            )
        conn.execute("BEGIN")
        grouped: dict[int, list[dict]] = {}
        no_replica = 0
        for match in eligible:
            match_id, db_kick = _kickoff_of_row(conn, match["match_uid"])
            plan_kick = parse_dt(match.get("kickoff_at"))
            kicks = [item for item in (plan_kick, db_kick) if item is not None]
            kickoff = min(kicks) if kicks else None
            if match_id is None:
                no_replica += 1
                continue
            state = _load_state(conn, match_id)
            item = {"match": match, "match_id": match_id, "kickoff": kickoff, "state": state}
            grouped.setdefault(_as_int(match.get("league_id")), []).append(item)

        leagues_fetched = 0
        leagues_cached = 0
        errors = 0
        written = 0
        refreshed = 0
        unavailable = 0
        skipped_frozen = 0
        skipped_after = 0
        unmatched = 0
        undo = []

        def consume(items: list[dict], kind: str, index: dict | None, fetched_at: datetime) -> None:
            nonlocal written, refreshed, unavailable, skipped_frozen, skipped_after, unmatched
            fetched_text = iso(fetched_at)
            current = clock()
            for item in items:
                action = decide(item["state"], item["kickoff"], fetched_at, current)
                if action == "skip":
                    if item["kickoff"] is None or current >= item["kickoff"] or fetched_at >= item["kickoff"]:
                        skipped_after += 1
                    else:
                        skipped_frozen += 1
                    continue
                assignment = _assignment_for_match(kind, index, item["match"], fetched_text)
                if assignment is None:
                    unmatched += 1
                    continue
                _apply_one(conn, item["match_id"], item["state"], action, assignment, fetched_text)
                if action == "refresh":
                    refreshed += 1
                else:
                    written += 1
                if assignment["status"] == "unavailable":
                    unavailable += 1
                undo.append(
                    {
                        "match_id": item["match_id"],
                        "match_uid": item["match"]["match_uid"],
                        "action": action,
                        "before": assignment.pop("_before"),
                        "after": {
                            "rank_home": assignment["home"],
                            "rank_away": assignment["away"],
                            "source_rank": SOURCE_RANK,
                            "as_of_rank": fetched_text,
                            "status": assignment["status"],
                        },
                    }
                )

        for league_id, items in grouped.items():
            cache_path = cache_dir / f"{league_id}.json"
            cache = _read_cache(cache_path)
            needs_network = False
            if not _cache_fresh(cache, moment):
                for item in items:
                    probe = decide(item["state"], item["kickoff"], moment, moment)
                    if probe in ("write", "refresh"):
                        needs_network = True
                        break
            if not needs_network and _cache_fresh(cache, moment):
                leagues_cached += 1
                fetched_at = parse_dt(cache.get("fetched_at"))
                kind = cache.get("kind")
                index = index_table(cache["data"]) if kind == "table" and isinstance(cache.get("data"), dict) else None
                if fetched_at is not None and kind in ("table", "unavailable"):
                    consume(items, kind, index, fetched_at)
                continue
            if not needs_network:
                for item in items:
                    action = decide(item["state"], item["kickoff"], moment, moment)
                    if action == "skip":
                        if item["kickoff"] is None or moment >= item["kickoff"]:
                            skipped_after += 1
                        else:
                            skipped_frozen += 1
                continue
            try:
                code, body = cl.get(f"standings_{league_id}", "/standings", {"league": league_id})
            except Exception:
                errors += 1
                continue
            fetched_at = clock()
            kind, data = classify_standings(code, body if isinstance(body, dict) else {})
            if kind == "table" and data is not None:
                got = _as_int(data.get("league_id"))
                if got is not None and got != league_id:
                    kind, data = "error", None
            if kind == "error":
                errors += 1
                continue
            leagues_fetched += 1
            index = index_table(data) if kind == "table" and data is not None else None
            _write_cache(
                cache_path,
                {
                    "league_id": league_id,
                    "fetched_at": iso(fetched_at),
                    "http": code,
                    "kind": kind,
                    "data": data,
                },
            )
            consume(items, kind, index, fetched_at)

        if undo:
            conn.commit()
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                with (cache_dir / "undo.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {"ts": iso(clock()), "ops": undo},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except OSError:
                # 名次已经提交。撤销日志写失败不把这次快照记成失败。
                pass
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if errors and (written or refreshed or leagues_fetched or leagues_cached):
        status = "partial"
    elif errors:
        status = "fail"
    else:
        status = "ok"
    return _summary(
        status=status,
        eligible=len(eligible),
        skipped_no_league=skipped_no_league,
        leagues_fetched=leagues_fetched,
        leagues_cached=leagues_cached,
        written=written,
        refreshed=refreshed,
        unavailable=unavailable,
        skipped_frozen=skipped_frozen,
        skipped_after_kickoff=skipped_after,
        unmatched=unmatched,
        no_replica_match=no_replica,
        errors=errors,
    )
