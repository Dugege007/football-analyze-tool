"""预期进球六列的整理、落库和数据表外壳。

预期进球属于基本面，与机构无关。数值只来自 BSD（bzzoiro）的赛事统计，
不在这里自建射门模型，也不把逐次射门写进宽表。

库和接口都保留供应商给出的完整精度。保留两位小数只发生在前端展示。
下半场优先使用 BSD 独立给出的 second_half。没有独立下半场时，在写入前用全场减去上半场，
主队和客队各自相减，不做四舍五入。接口只读取已经写入的下半场数值，不再减一次。
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app import reason_codes
from app.db import split_sql

TZ_CN = timezone(timedelta(hours=8))
KIND = "xg"
SOURCE = "bsd"
SCOPE_POSTMATCH = "postmatch_actual"
DAILY_CALL_CAP = 7500
RETRY_AFTER = timedelta(hours=3)
ERROR_RETRY_AFTER = timedelta(minutes=30)

# 数据表 fundamentals 的六个键，顺序与页面列一致。
COLUMN_KEYS: tuple[str, ...] = (
    "xg_ft_home",
    "xg_ft_away",
    "xg_ht_home",
    "xg_ht_away",
    "xg_2h_home",
    "xg_2h_away",
)
_COLUMN_PATHS: tuple[tuple[str, str, str], ...] = (
    ("xg_ft_home", "ft", "home"),
    ("xg_ft_away", "ft", "away"),
    ("xg_ht_home", "ht", "home"),
    ("xg_ht_away", "ht", "away"),
    ("xg_2h_home", "h2", "home"),
    ("xg_2h_away", "h2", "away"),
)

REASON_NO_MAPPING = "no_bsd_mapping"
REASON_NO_COVERAGE = "no_bsd_coverage"
REASON_FULL_SIDE_MISSING = "full_side_missing"
REASON_HALF_SIDE_MISSING = "half_side_missing"
REASON_SECOND_HALF_INPUTS_MISSING = "second_half_inputs_missing"
REASON_DERIVED = "derived_from_full_minus_half"
REASON_NOT_COLLECTED = "not_collected"

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "schema" / "v2_0_xg_obs.sql"
_PERIOD_FROM_BSD = {"full": "ft", "first_half": "ht", "second_half": "h2"}


def iso_cn(moment: datetime) -> str:
    """北京时间的秒级时刻字符串。无时区的输入按北京时间理解。"""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TZ_CN)
    return moment.astimezone(TZ_CN).isoformat(timespec="seconds")


def ensure_xg_schema(conn: sqlite3.Connection) -> None:
    """补齐观测表、赛事映射和拉取队列。逐条执行，避免打断调用方已经打开的事务。"""
    for statement in split_sql(_SCHEMA_PATH.read_text(encoding="utf-8")):
        conn.execute(statement)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _as_float(value: Any) -> Optional[float]:
    """只接受有限的实数。布尔值不是数值。缺失返回空，不用零顶上。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    return None


def _read_xg_block(node: Any) -> tuple[Optional[float], Optional[bool]]:
    """从一侧统计里读出预期进球和是否为估算。

    BSD 的形状是 ``{"xg": {"actual": 数值, "estimated": 真或假}}``。
    也接受这一侧本身就是 ``{"actual", "estimated"}``，或预期进球是一个裸数字。
    """
    if node is None:
        return None, None
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        return _as_float(node), None
    if not isinstance(node, dict):
        return None, None
    block = node.get("xg") if "xg" in node else node
    if isinstance(block, (int, float)) and not isinstance(block, bool):
        return _as_float(block), None
    if not isinstance(block, dict):
        return None, None
    estimated = block.get("estimated")
    flag = estimated if isinstance(estimated, bool) else None
    if "actual" in block:
        return _as_float(block.get("actual")), flag
    return None, flag


def _side_node(stats: dict, period: str, side: str) -> Any:
    if period == "full":
        return stats.get(side)
    block = stats.get(period)
    if not isinstance(block, dict):
        return None
    return block.get(side)


def _quality_of(value: Optional[float], estimated: Optional[bool], *, derived: bool) -> Optional[str]:
    if derived and value is not None:
        return "derived"
    if value is None:
        return None
    if estimated is True:
        return "estimated"
    if estimated is False:
        return "measured"
    return None


def _rollup(qualities: list[Optional[str]]) -> str:
    present = [item for item in qualities if item]
    if not present:
        return "unavailable"
    unique = set(present)
    if len(unique) == 1:
        return unique.pop()
    return "mixed"


def build_payload_from_bsd_stats(body: Any, *, provider_event_id: int, as_of: str) -> dict:
    """把 BSD 赛事统计整理成 stats_obs 的载荷。

    下半场：某一侧已经给出独立的 second_half 时原样保留；没有独立值时，
    仅当这一侧的全场和上半场都有数值，才用全场减去上半场。任一侧缺失则下半场留空，不用零顶上。
    射门明细、逐分钟曲线不进入载荷。
    """
    stats = body.get("stats") if isinstance(body, dict) else None
    if not isinstance(stats, dict):
        stats = {}

    slots: dict[str, dict[str, dict[str, Any]]] = {}
    for period in ("full", "first_half", "second_half"):
        key = _PERIOD_FROM_BSD[period]
        slots[key] = {}
        for side in ("home", "away"):
            value, estimated = _read_xg_block(_side_node(stats, period, side))
            slots[key][side] = {"value": value, "estimated": estimated, "derived": False}

    origins: dict[str, Optional[str]] = {}
    for side in ("home", "away"):
        if slots["h2"][side]["value"] is not None:
            origins[side] = "bsd"
            continue
        full_value = slots["ft"][side]["value"]
        half_value = slots["ht"][side]["value"]
        if full_value is None or half_value is None:
            origins[side] = None
            slots["h2"][side] = {"value": None, "estimated": None, "derived": False}
            continue
        # 全精度相减。这里禁止四舍五入到两位小数。
        slots["h2"][side] = {
            "value": full_value - half_value,
            "estimated": None,
            "derived": True,
        }
        origins[side] = "derived"

    quality_by_side: dict[str, dict[str, Optional[str]]] = {}
    period_quality: dict[str, str] = {}
    for period in ("ft", "ht", "h2"):
        quality_by_side[period] = {}
        for side in ("home", "away"):
            slot = slots[period][side]
            quality_by_side[period][side] = _quality_of(
                slot["value"], slot["estimated"], derived=bool(slot["derived"])
            )
        period_quality[period] = _rollup(list(quality_by_side[period].values()))

    values = {
        period: {"home": slots[period]["home"]["value"], "away": slots[period]["away"]["value"]}
        for period in ("ft", "ht", "h2")
    }
    any_value = any(values[period][side] is not None for period in values for side in ("home", "away"))
    payload = {
        "ft": values["ft"],
        "ht": values["ht"],
        "h2": values["h2"],
        "quality": _rollup([period_quality[item] if period_quality[item] != "unavailable" else None for item in ("ft", "ht", "h2")])
        if any_value else "unavailable",
        "quality_detail": period_quality,
        "quality_by_side": quality_by_side,
        "h2_origin": origins,
        "source": SOURCE,
        "provider_event_id": int(provider_event_id),
        "scope": SCOPE_POSTMATCH,
        "as_of": as_of,
    }
    if not any_value:
        payload["reason"] = REASON_NO_COVERAGE
    return payload


def _shell(
    *,
    value: Optional[float],
    status: str,
    source: Optional[str],
    as_of: Optional[str],
    reason: Optional[str],
    quality: Optional[str],
) -> dict:
    return {
        "value": value,
        "status": status,
        "source": source,
        "as_of": as_of,
        "reason": reason,
        "reason_zh": reason_codes.zh(reason),
        "quality": quality,
    }


def _not_filled_shells(reason: Optional[str] = None) -> dict[str, dict]:
    return {
        key: _shell(value=None, status="not_filled", source=None, as_of=None, reason=reason, quality=None)
        for key in COLUMN_KEYS
    }


def _unavailable_shells(reason: str, *, source: Optional[str] = None, as_of: Optional[str] = None) -> dict[str, dict]:
    return {
        key: _shell(value=None, status="unavailable", source=source, as_of=as_of, reason=reason, quality=None)
        for key in COLUMN_KEYS
    }


def _side_quality(payload: dict, period: str, side: str) -> Optional[str]:
    by_side = payload.get("quality_by_side")
    if isinstance(by_side, dict):
        period_map = by_side.get(period)
        if isinstance(period_map, dict) and side in period_map:
            quality = period_map.get(side)
            return quality if isinstance(quality, str) else None
    detail = payload.get("quality_detail")
    if isinstance(detail, dict):
        quality = detail.get(period)
        if isinstance(quality, str) and quality not in ("mixed", "unavailable"):
            return quality
    return None


def _period_value(payload: dict, period: str, side: str) -> Optional[float]:
    block = payload.get(period)
    if not isinstance(block, dict):
        return None
    return _as_float(block.get(side))


def shells_for_match(
    *,
    finished: bool,
    schema_ready: bool,
    mapped: bool,
    observation: Optional[dict],
) -> dict[str, dict]:
    """按列组装六个外壳。六列不共用同一个状态。

    未完赛一律未填写，即使库里已经有赛后观测也不向外返回，避免赛后数值提前出现。
    映射表还没有建立时保持未填写，避免在迁移之前把全部完赛场次显示成无法获取。
    映射表已在、但这场没有赛事编号时，六列都是无法获取，原因是 no_bsd_mapping。
    """
    if not finished:
        return _not_filled_shells()
    if not schema_ready:
        return _not_filled_shells(REASON_NOT_COLLECTED)
    if not mapped:
        return _unavailable_shells(REASON_NO_MAPPING)
    if not observation:
        return _not_filled_shells(REASON_NOT_COLLECTED)

    payload = observation.get("payload") if isinstance(observation.get("payload"), dict) else {}
    as_of = observation.get("as_of") or payload.get("as_of")
    as_of = as_of if isinstance(as_of, str) else None
    source = observation.get("source") if isinstance(observation.get("source"), str) else SOURCE

    numbers = [_period_value(payload, period, side) for period in ("ft", "ht", "h2") for side in ("home", "away")]
    if all(item is None for item in numbers):
        reason = payload.get("reason") if isinstance(payload.get("reason"), str) else REASON_NO_COVERAGE
        return _unavailable_shells(reason, source=source, as_of=as_of)

    origins = payload.get("h2_origin") if isinstance(payload.get("h2_origin"), dict) else {}
    shells: dict[str, dict] = {}
    for key, period, side in _COLUMN_PATHS:
        value = _period_value(payload, period, side)
        quality = _side_quality(payload, period, side)
        if value is None:
            if period == "h2":
                reason = REASON_SECOND_HALF_INPUTS_MISSING
            elif period == "ht":
                reason = REASON_HALF_SIDE_MISSING
            else:
                reason = REASON_FULL_SIDE_MISSING
            shells[key] = _shell(
                value=None, status="unavailable", source=source, as_of=as_of, reason=reason, quality=None
            )
            continue
        reason = REASON_DERIVED if period == "h2" and origins.get(side) == "derived" else None
        if reason == REASON_DERIVED:
            quality = "derived"
        shells[key] = _shell(
            value=value, status="ok", source=source, as_of=as_of, reason=reason, quality=quality
        )
    return shells


def flat_columns(fundamentals: Any) -> dict[str, Any]:
    """扁平导出使用与嵌套相同的六个列名。数值列就是这六个键，其余字段加后缀。

    每一行都带齐六个列名，缺观测时值为空，避免列集合随场次变化。
    """
    funds = fundamentals if isinstance(fundamentals, dict) else {}
    out: dict[str, Any] = {}
    for key in COLUMN_KEYS:
        shell = funds.get(key) if isinstance(funds.get(key), dict) else {}
        out[key] = shell.get("value")
        out[f"{key}_status"] = shell.get("status")
        out[f"{key}_source"] = shell.get("source")
        out[f"{key}_as_of"] = shell.get("as_of")
        out[f"{key}_reason"] = shell.get("reason")
        out[f"{key}_quality"] = shell.get("quality")
    return out


class XgMatchContext:
    """一页比赛的映射和最新观测。表不存在时 schema_ready 为假，读路径不写库。"""

    def __init__(self, schema_ready: bool, rows: dict[int, dict]):
        self.schema_ready = schema_ready
        self._rows = rows

    def for_match(self, match_id: int) -> dict:
        row = self._rows.get(match_id) or {}
        return {
            "schema_ready": self.schema_ready,
            "mapped": bool(row.get("mapped")),
            "observation": row.get("observation"),
        }


def load_match_context(conn: sqlite3.Connection, match_ids: list[int]) -> XgMatchContext:
    """只读。没有映射表时不把完赛场次判成无法获取。"""
    if not match_ids:
        names = _table_names(conn)
        return XgMatchContext("bsd_event_map" in names, {})
    names = _table_names(conn)
    schema_ready = "bsd_event_map" in names
    found: dict[int, dict] = {match_id: {"mapped": False, "observation": None} for match_id in match_ids}
    placeholders = ",".join("?" for _ in match_ids)
    if schema_ready:
        for row in conn.execute(
            f"SELECT match_id, event_id FROM bsd_event_map WHERE match_id IN ({placeholders})",
            match_ids,
        ):
            found[row[0]]["mapped"] = True
            found[row[0]]["event_id"] = row[1]
    if "stats_obs" in names:
        for row in conn.execute(
            f"SELECT match_id, as_of, source, payload_json FROM stats_obs "
            f"WHERE kind = ? AND match_id IN ({placeholders}) ORDER BY match_id ASC, as_of DESC",
            [KIND, *match_ids],
        ):
            slot = found[row[0]]
            if slot.get("observation") is not None:
                continue
            try:
                payload = json.loads(row[3]) if row[3] else {}
            except json.JSONDecodeError:
                payload = {}
            slot["observation"] = {"as_of": row[1], "source": row[2], "payload": payload}
    return XgMatchContext(schema_ready, found)


def upsert_xg_observation(
    conn: sqlite3.Connection,
    match_id: int,
    payload: dict,
    *,
    as_of: str,
    fetched_at: str,
) -> None:
    """写入或覆盖同一比赛、同一时刻、同一来源的预期进球观测。数值原样进 JSON，不四舍五入。"""
    ensure_xg_schema(conn)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        "INSERT INTO stats_obs (match_id, kind, as_of, source, payload_json, provider_ref, fetched_at, quality) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(match_id, kind, as_of, source) DO UPDATE SET "
        "payload_json = excluded.payload_json, provider_ref = excluded.provider_ref, "
        "fetched_at = excluded.fetched_at, quality = excluded.quality",
        (
            match_id,
            KIND,
            as_of,
            SOURCE,
            encoded,
            str(payload.get("provider_event_id") or ""),
            fetched_at,
            payload.get("quality"),
        ),
    )


def enqueue_finished_matches(conn: sqlite3.Connection, match_ids: list[int], now: datetime) -> dict:
    """完赛场次进入预期进球队列。已在队列里的不重置，避免把已经排好的三小时补拉提前。"""
    ensure_xg_schema(conn)
    moment = iso_cn(now)
    inserted = 0
    for match_id in match_ids:
        cursor = conn.execute(
            "INSERT INTO xg_fetch_queue (match_id, due_at, attempts, max_attempts, created_at, updated_at) "
            "VALUES (?, ?, 0, 2, ?, ?) ON CONFLICT(match_id) DO NOTHING",
            (match_id, moment, moment, moment),
        )
        inserted += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    return {"enqueued": inserted}


def _mark_queue(
    conn: sqlite3.Connection,
    match_id: int,
    *,
    now: datetime,
    attempts: int,
    max_attempts: int,
    status: str,
    error: Optional[str],
    schedule_retry: bool,
) -> None:
    moment = iso_cn(now)
    if schedule_retry and attempts < max_attempts:
        due = iso_cn(now + RETRY_AFTER)
        conn.execute(
            "UPDATE xg_fetch_queue SET due_at = ?, attempts = ?, last_status = ?, last_error = ?, "
            "done_at = NULL, updated_at = ? WHERE match_id = ?",
            (due, attempts, status, error, moment, match_id),
        )
        return
    conn.execute(
        "UPDATE xg_fetch_queue SET attempts = ?, last_status = ?, last_error = ?, done_at = ?, updated_at = ? "
        "WHERE match_id = ?",
        (attempts, status, error, moment, moment, match_id),
    )


def _delay_queue(conn: sqlite3.Connection, match_id: int, now: datetime, error: str) -> None:
    moment = iso_cn(now)
    due = iso_cn(now + ERROR_RETRY_AFTER)
    conn.execute(
        "UPDATE xg_fetch_queue SET due_at = ?, last_status = ?, last_error = ?, updated_at = ? WHERE match_id = ?",
        (due, "error", error, moment, match_id),
    )


def store_fetch_result(
    conn: sqlite3.Connection,
    match_id: int,
    event_id: int,
    fetch_result: dict,
    now: datetime,
) -> str:
    """根据一次拉取结果写观测。成功或确认无覆盖返回状态；网络和服务器错误不写空观测。"""
    status = fetch_result.get("http_status")
    body = fetch_result.get("body")
    moment = iso_cn(now)
    if status == 200 and isinstance(body, dict):
        payload = build_payload_from_bsd_stats(body, provider_event_id=event_id, as_of=moment)
        upsert_xg_observation(conn, match_id, payload, as_of=moment, fetched_at=moment)
        if payload.get("quality") == "unavailable":
            return "unavailable"
        return "stored"
    if status in (404, 204) or status == 200:
        payload = build_payload_from_bsd_stats({}, provider_event_id=event_id, as_of=moment)
        upsert_xg_observation(conn, match_id, payload, as_of=moment, fetched_at=moment)
        return "unavailable"
    return "error"


def pull_due_expected_goals(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    client: Any = None,
    quota_path: Optional[Path] = None,
    limit: int = 20,
    sleep_seconds: float = 0,
    daily_cap: int = DAILY_CALL_CAP,
) -> dict:
    """拉取已到期的完赛场次。没有令牌时什么都不改，队列留到下次。

    每次成功的拉取如果还没达到两次，会把下一次到期时间改成三小时之后。
    没有赛事映射的场次不请求 BSD，直接结束，避免浪费每日额度。
    """
    import time

    ensure_xg_schema(conn)
    if client is None:
        client = client_from_environment()
    report: dict[str, Any] = {
        "fetched": 0,
        "stored": 0,
        "unavailable": 0,
        "no_bsd_mapping": 0,
        "skipped_no_token": False,
        "stopped_quota": False,
        "errors": 0,
    }
    if client is None:
        report["skipped_no_token"] = True
        return report

    moment = iso_cn(now)
    rows = conn.execute(
        "SELECT match_id, attempts, max_attempts FROM xg_fetch_queue "
        "WHERE done_at IS NULL AND due_at <= ? ORDER BY due_at ASC, match_id ASC LIMIT ?",
        (moment, int(limit)),
    ).fetchall()
    ledger = quota_path or default_quota_path()
    for index, row in enumerate(rows):
        match_id, attempts, max_attempts = int(row[0]), int(row[1]), int(row[2])
        mapped = conn.execute(
            "SELECT event_id FROM bsd_event_map WHERE match_id = ?",
            (match_id,),
        ).fetchone()
        if mapped is None:
            _mark_queue(
                conn, match_id, now=now, attempts=attempts, max_attempts=max_attempts,
                status="no_bsd_mapping", error=None, schedule_retry=False,
            )
            report["no_bsd_mapping"] += 1
            continue
        if not reserve_daily_call(ledger, now, cap=daily_cap):
            report["stopped_quota"] = True
            break
        if index > 0 and sleep_seconds > 0:
            time.sleep(sleep_seconds)
        event_id = int(mapped[0])
        try:
            fetched = client.fetch_event_stats(event_id)
        except Exception:
            fetched = {"http_status": None, "body": None, "error": "client_error"}
        outcome = store_fetch_result(conn, match_id, event_id, fetched, now)
        report["fetched"] += 1
        if outcome == "error":
            report["errors"] += 1
            _delay_queue(conn, match_id, now, str(fetched.get("error") or "request_failed"))
            if fetched.get("http_status") == 429:
                report["stopped_quota"] = True
                break
            continue
        report[outcome] += 1
        _mark_queue(
            conn, match_id, now=now, attempts=attempts + 1, max_attempts=max_attempts,
            status=outcome, error=None, schedule_retry=True,
        )
    return report


def pending_backfill_pairs(conn: sqlite3.Connection, limit: int) -> list[tuple[int, int]]:
    """已完赛、已有赛事映射、最新观测还不是可用数值的场次。返回比赛编号和 BSD 赛事编号。"""
    names = _table_names(conn)
    if "bsd_event_map" not in names or "matches" not in names or "results" not in names:
        return []
    rows = conn.execute(
        "SELECT m.id, map.event_id FROM matches m "
        "JOIN results r ON r.match_id = m.id "
        "JOIN bsd_event_map map ON map.match_id = m.id "
        "WHERE r.home_goals IS NOT NULL AND r.away_goals IS NOT NULL "
        "ORDER BY m.id"
    ).fetchall()
    latest: dict[int, str] = {}
    if "stats_obs" in names:
        for row in conn.execute(
            "SELECT match_id, payload_json FROM stats_obs WHERE kind = ? ORDER BY match_id ASC, as_of DESC",
            (KIND,),
        ):
            if row[0] not in latest:
                latest[row[0]] = row[1]
    pairs: list[tuple[int, int]] = []
    for match_id, event_id in rows:
        raw = latest.get(match_id)
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict) and payload.get("quality") != "unavailable":
                continue
            if isinstance(payload, dict) and payload.get("quality") == "unavailable":
                continue
        pairs.append((int(match_id), int(event_id)))
        if len(pairs) >= limit:
            break
    return pairs


def classify_backfill(conn: sqlite3.Connection) -> dict:
    """历史回填的干跑分类。只读，不建表，不请求 BSD。"""
    names = _table_names(conn)
    if "matches" not in names or "results" not in names:
        return {"ok": False, "reason": "库里没有比赛表或赛果表"}
    finished = [
        row[0]
        for row in conn.execute(
            "SELECT m.id FROM matches m JOIN results r ON r.match_id = m.id "
            "WHERE r.home_goals IS NOT NULL AND r.away_goals IS NOT NULL ORDER BY m.id"
        )
    ]
    reasons: dict[str, int] = {
        REASON_NO_MAPPING: 0,
        REASON_NOT_COLLECTED: 0,
        "stored": 0,
        REASON_NO_COVERAGE: 0,
    }
    if "bsd_event_map" not in names:
        reasons[REASON_NOT_COLLECTED] = len(finished)
        return {
            "ok": True,
            "schema_ready": False,
            "finished_matches": len(finished),
            "would_fetch": 0,
            "unavailable_reasons": reasons,
        }
    mapped = {
        row[0]: int(row[1])
        for row in conn.execute("SELECT match_id, event_id FROM bsd_event_map")
    }
    stored_ids: set[int] = set()
    unavailable_ids: set[int] = set()
    if "stats_obs" in names and finished:
        placeholders = ",".join("?" for _ in finished)
        seen: set[int] = set()
        for row in conn.execute(
            f"SELECT match_id, payload_json FROM stats_obs WHERE kind = ? AND match_id IN ({placeholders}) "
            "ORDER BY match_id ASC, as_of DESC",
            [KIND, *finished],
        ):
            if row[0] in seen:
                continue
            seen.add(row[0])
            try:
                payload = json.loads(row[1]) if row[1] else {}
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict) and payload.get("quality") == "unavailable":
                unavailable_ids.add(row[0])
            elif isinstance(payload, dict):
                stored_ids.add(row[0])
    would_fetch = 0
    for match_id in finished:
        if match_id not in mapped:
            reasons[REASON_NO_MAPPING] += 1
            continue
        if match_id in stored_ids:
            reasons["stored"] += 1
            continue
        if match_id in unavailable_ids:
            reasons[REASON_NO_COVERAGE] += 1
            continue
        reasons[REASON_NOT_COLLECTED] += 1
        would_fetch += 1
    return {
        "ok": True,
        "schema_ready": True,
        "finished_matches": len(finished),
        "with_mapping": sum(1 for match_id in finished if match_id in mapped),
        "would_fetch": would_fetch,
        "unavailable_reasons": reasons,
    }


def default_quota_path() -> Path:
    """每日额度账本。与盘口补数的额度账本不是同一个文件。"""
    root = os.environ.get("ODDS_DATA_DIR", "").strip()
    base = Path(root) if root else Path(__file__).resolve().parents[2] / "data" / "odds-data"
    return base / "backfill" / "xg-bsd-queue" / "quota.json"


def reserve_daily_call(path: Path, now: datetime, *, cap: int = DAILY_CALL_CAP) -> bool:
    """按协调世界时的日期计数。达到上限时返回假，并且不把计数再加一。"""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ_CN)
    day = now.astimezone(timezone.utc).date().isoformat()
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            raw = handle.read()
            try:
                data = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                data = {}
            if data.get("day") != day:
                data = {"day": day, "count": 0}
            count = int(data.get("count") or 0)
            if count >= cap:
                return False
            data["count"] = count + 1
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(data, ensure_ascii=False))
            return True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class BsdStatsClient:
    """BSD 赛事统计客户端。令牌只放在请求头里，不写进错误信息。"""

    def __init__(
        self,
        token: str,
        *,
        opener: Any = None,
        base_url: str = "https://sports.bzzoiro.com/api/v2",
        timeout: float = 30,
    ):
        self._token = token
        self._opener = opener
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def fetch_event_stats(self, event_id: int) -> dict:
        """请求 ``GET /api/v2/events/{id}/stats/``。返回状态码、解析后的对象和错误类别。"""
        url = f"{self._base_url}/events/{int(event_id)}/stats/"
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Token {self._token}", "Accept": "application/json"},
        )
        try:
            open_fn = self._opener.open if self._opener is not None else urllib.request.urlopen
            with open_fn(request, timeout=self._timeout) as response:
                raw = response.read()
                status = getattr(response, "status", 200)
                if not raw:
                    return {"http_status": status, "body": None, "error": None}
                return {"http_status": status, "body": json.loads(raw.decode("utf-8")), "error": None}
        except urllib.error.HTTPError as exc:
            return {"http_status": exc.code, "body": None, "error": f"http_{exc.code}"}
        except urllib.error.URLError:
            return {"http_status": None, "body": None, "error": "network"}
        except json.JSONDecodeError:
            return {"http_status": 200, "body": None, "error": "bad_json"}


def client_from_environment() -> Optional[BsdStatsClient]:
    """没有 ``BSD_BZZOIRO_API_KEY`` 时返回空，调用方保持未填写，不发请求。"""
    token = os.environ.get("BSD_BZZOIRO_API_KEY", "").strip()
    if not token:
        return None
    return BsdStatsClient(token)
