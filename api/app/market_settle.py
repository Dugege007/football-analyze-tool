"""机构赛果：用各机构自己的临盘对照完场比分，计算赢盘方并写入 market_settle。

口径以《机构赛果落库口径》（2026-10-15）第 3、4、5 节为准。

本模块只复用 app.backtest 的 quarter_split 与 settle_code，不另写拆盘公式。
亚洲让球盘的盘口使用仓库记法：正数表示主队让球，负数表示主队受让，零表示平手。
从 odds_snapshot 读到的亚洲让球盘盘口是接口记法（负数表示主队让球），写入前先取反。
大小球的五态结算码必须写成 settle_code(总进球, 0, 进球线, "主")，不能传入「大」或「小」。
settle_code 返回的是二元组 (结算码, 系数)，落库和接口只保留结算码字符串。

预测结算（settlement / settlement_by_book）不在本模块里，缺临盘时也不退回澳门。
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from app import backtest as bt
from app import reason_codes

# 与 collection_schedule.TZ_CN 相同的东八区，避免本模块再去依赖数据表模块。
TZ_CN = timezone(timedelta(hours=8))

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "docs" / "schema" / "v2_1_market_settle.sql"

# 五家机构，顺序与数据表列一致。不许用另一家的临盘填补空缺。
BOOKS: tuple[str, ...] = ("macau", "crown", "pinnacle", "william", "bet365")
MARKETS: tuple[str, ...] = ("ah", "ou", "x1x2")

# 规则临盘优先；同一机构的旧规则通道只在规则临盘缺失时使用，仍然不是跨机构顶替。
CLOSE_CHANNELS: tuple[str, ...] = ("rule", "rule_legacy")
_CHANNEL_RANK = {name: index for index, name in enumerate(CLOSE_CHANNELS)}

# 快照里的市场代码。大小球在库里常写作 ou，口径文档也允许 goalline。欧盘同理。
_OU_MARKETS = ("goalline", "ou")
_EURO_MARKETS = ("euro_1x2", "1x2")
_AH_MARKETS = ("asian",)

# 阻止结算的未解决标记。退款、推迟、取消、腰斩与现网赛果隐藏一致。
# 加时或点球的九十分钟比分未核实时，不得用该比分计算机构赛果。
BLOCKING_FLAGS: tuple[str, ...] = (
    "refund",
    "postponed",
    "cancelled",
    "abandoned",
    "extra_time_unverified",
)

_LINE_CELL_KEYS = (
    "line", "side", "push", "settle_code", "display_text",
    "cell_status", "null_reason", "null_reason_zh",
)
_EURO_CELL_KEYS = (
    "side", "odds", "display_text", "cell_status", "null_reason", "null_reason_zh",
)
_EURO_LABEL = {"home": "胜", "draw": "平", "away": "负"}

_RECORD_COLUMNS = (
    "match_id", "book", "market",
    "line", "home_water", "away_water", "over_water", "under_water",
    "odds_home", "odds_draw", "odds_away", "odds_used",
    "home_goals", "away_goals",
    "side", "push", "settle_code", "display_text",
    "cell_status", "null_reason", "null_reason_zh", "line_source", "computed_at",
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """创建 market_settle。语句可以重复执行，不会改已有比赛行。"""
    from app.db import apply_sql_idempotent

    apply_sql_idempotent(conn, SCHEMA_SQL)


def outcome_1x2(home_goals: int, away_goals: int) -> str:
    """完场胜平负：home 胜、draw 平、away 负。只比较全场进球，没有半盘。"""
    if home_goals > away_goals:
        return "home"
    if home_goals == away_goals:
        return "draw"
    return "away"


def is_quarter_step(line: float) -> bool:
    """仓库约定盘口步进是 0.25。不是这个步进时不得调用 quarter_split 硬算。"""
    scaled = float(line) * 4.0
    return math.isfinite(scaled) and abs(scaled - round(scaled)) < 1e-6


def snapshot_asian_line_to_warehouse(line: float) -> float:
    """接口记法转为仓库记法：取反。零仍是零，避免出现负零。"""
    value = -float(line)
    if value == 0.0:
        return 0.0
    return value


def sum_half_signs(margins: Sequence[float]) -> int:
    """对各半盘的正负号求和。每个半盘加一次，不把 0.5 权重乘进这个和。"""
    total = 0
    for margin in margins:
        total += _margin_sign(margin)
    return total


def settle_asian(home_goals: int, away_goals: int, line: float) -> dict[str, Any]:
    """计算一家机构的亚洲让球盘赛果。line 必须已经是仓库记法。"""
    numeric = _as_float(line)
    if numeric is None:
        return _line_cell(None, unavailable=True, reason="not_captured")
    if not is_quarter_step(numeric):
        cell = _line_cell(None, unavailable=True, reason="invalid_line_step")
        cell["line"] = numeric
        return cell
    goal_diff = int(home_goals) - int(away_goals)
    sign_sum = _sign_sum_against_line(float(goal_diff), numeric)
    side, push = _side_from_sign_sum(sign_sum, "home", "away")
    # 只保留二元组里的结算码。参考侧是主队。
    code, _factor = bt.settle_code(int(home_goals), int(away_goals), numeric, "主")
    display = "走水" if push else _format_asian_display(side, numeric)
    return _line_cell(numeric, side=side, push=push, settle_code=code, display_text=display)


def settle_totals(total_goals: int, line: float) -> dict[str, Any]:
    """计算大小球赛果。五态相对大球，调用方式固定为 settle_code(总进球, 0, 进球线, "主")。"""
    numeric = _as_float(line)
    if numeric is None:
        return _line_cell(None, unavailable=True, reason="not_captured")
    if not is_quarter_step(numeric):
        cell = _line_cell(None, unavailable=True, reason="invalid_line_step")
        cell["line"] = numeric
        return cell
    sign_sum = _sign_sum_against_line(float(int(total_goals)), numeric)
    side, push = _side_from_sign_sum(sign_sum, "over", "under")
    # 大球被映射成主队视角：净胜球等于总进球减零。方向参数只能是「主」，不能是大球或小球。
    code, _factor = bt.settle_code(int(total_goals), 0, numeric, "主")
    display = "走水" if push else _format_totals_display(side, numeric)
    return _line_cell(numeric, side=side, push=push, settle_code=code, display_text=display)


def settle_1x2(home_goals: int, away_goals: int, odds_home: float, odds_draw: float, odds_away: float) -> dict[str, Any]:
    """欧盘只比胜平负。三项赔率必须都已经存在；本函数不接受只有主胜的半格。"""
    side = outcome_1x2(int(home_goals), int(away_goals))
    odds = {"home": float(odds_home), "draw": float(odds_draw), "away": float(odds_away)}[side]
    label = _EURO_LABEL[side]
    return {
        "side": side,
        "odds": odds,
        "display_text": f"{label} {format_decimal_odds(odds)}",
        "cell_status": "ok",
        "null_reason": None,
        "null_reason_zh": None,
    }


def blank_by_book() -> dict[str, dict[str, dict[str, Any]]]:
    """未完赛扁平列用的空模板。键齐全，状态留空，页面仍显示横线而不是「无法获取」。"""
    return {
        book: {
            "ah": _empty_line_cell(),
            "ou": _empty_line_cell(),
            "x1x2": _empty_euro_cell(),
        }
        for book in BOOKS
    }


def unavailable_by_book(reason: str) -> dict[str, dict[str, dict[str, Any]]]:
    """已完赛但整场不能结算时，五家机构三个市场都写成同一个空原因。"""
    out = blank_by_book()
    for book in BOOKS:
        out[book]["ah"] = _line_cell(None, unavailable=True, reason=reason)
        out[book]["ou"] = _line_cell(None, unavailable=True, reason=reason)
        out[book]["x1x2"] = _euro_cell(unavailable=True, reason=reason)
    return out


def ensure_by_book_keys(by_book: Mapping[str, Any] | None) -> dict[str, dict[str, dict[str, Any]]]:
    """补齐五家机构与三个市场的键，已有值保持不变。供扁平格式保持列集合一致。"""
    filled = blank_by_book()
    if not isinstance(by_book, Mapping):
        return filled
    for book in BOOKS:
        source_book = by_book.get(book)
        if not isinstance(source_book, Mapping):
            continue
        for market in MARKETS:
            source_cell = source_book.get(market)
            if not isinstance(source_cell, Mapping):
                continue
            template = filled[book][market]
            for key in tuple(template):
                if key in source_cell:
                    template[key] = source_cell[key]
    return filled


def load_blocking_flags(conn: sqlite3.Connection) -> dict[int, str]:
    """未解决的特殊场次标记。表不存在时返回空字典，避免正式库缺表时接口报错。"""
    if not _has_table(conn, "result_ingest_flags"):
        return {}
    placeholders = ",".join("?" for _ in BLOCKING_FLAGS)
    rows = conn.execute(
        f"SELECT match_id, flag FROM result_ingest_flags "
        f"WHERE resolved_at IS NULL AND flag IN ({placeholders})",
        BLOCKING_FLAGS,
    ).fetchall()
    rank = {flag: index for index, flag in enumerate(BLOCKING_FLAGS)}
    out: dict[int, str] = {}
    for row in rows:
        match_id = int(row["match_id"] if hasattr(row, "keys") else row[0])
        flag = row["flag"] if hasattr(row, "keys") else row[1]
        if match_id not in out or rank[flag] < rank[out[match_id]]:
            out[match_id] = flag
    return out


def load_extra_time_unverified(conn: sqlite3.Connection) -> set[int]:
    """九十分钟口径未核实的场次。这些场次不能用库里的比分计算机构赛果。"""
    return {match_id for match_id, flag in load_blocking_flags(conn).items() if flag == "extra_time_unverified"}


def load_by_book(conn: sqlite3.Connection, match_ids: Sequence[int]) -> dict[int, dict]:
    """读取已经落库的机构赛果。没有表、或这场没有任何行时，不返回该场。"""
    if not match_ids or not _has_table(conn, "market_settle"):
        return {}
    grouped: dict[int, list[sqlite3.Row]] = {}
    for chunk in _chunks(match_ids):
        placeholders = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT * FROM market_settle WHERE match_id IN ({placeholders})",
            tuple(chunk),
        ):
            grouped.setdefault(int(row["match_id"]), []).append(row)
    return {match_id: _rows_to_by_book(rows) for match_id, rows in grouped.items()}


def compute_by_book_many(
    conn: sqlite3.Connection,
    scores: Mapping[int, tuple[int, int]],
    *,
    now: datetime | str | None = None,
) -> dict[int, dict]:
    """按完场比分现算五家机构三个市场。只读临盘，不写 market_settle。"""
    if not scores:
        return {}
    quotes = load_close_quotes(conn, list(scores))
    computed_at = format_computed_at(now)
    out: dict[int, dict] = {}
    for match_id, (home_goals, away_goals) in scores.items():
        records = compute_match_records(
            int(match_id), int(home_goals), int(away_goals), quotes, computed_at=computed_at,
        )
        out[int(match_id)] = _records_to_by_book(records)
    return out


def load_close_quotes(conn: sqlite3.Connection, match_ids: Sequence[int]) -> dict[tuple[int, str, str], dict[str, Any]]:
    """读取各机构自己的规则临盘。亚洲让球盘优先 odds_asian，缺失时才读快照并取反。"""
    wanted = [int(match_id) for match_id in match_ids]
    if not wanted:
        return {}
    asian = _load_asian_close(conn, wanted)
    totals = _load_totals_table(conn, wanted)
    euro_home = _load_euro_home_only(conn, wanted)
    snapshots = _load_close_snapshots(conn, wanted)
    quotes: dict[tuple[int, str, str], dict[str, Any]] = {}
    for match_id in wanted:
        for book in BOOKS:
            book_snaps = snapshots.get((match_id, book), [])
            quotes[(match_id, book, "ah")] = choose_asian_close(asian.get((match_id, book)), book_snaps)
            quotes[(match_id, book, "ou")] = choose_totals_close(totals.get((match_id, book)), book_snaps)
            quotes[(match_id, book, "x1x2")] = choose_euro_close(euro_home.get((match_id, book)), book_snaps)
    return quotes


def choose_asian_close(asian_row: Mapping[str, Any] | None, snapshot_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """一家机构的亚洲让球盘临盘。有仓库盘口就用仓库盘口；否则把快照盘口取反。"""
    quote = _empty_quote()
    if asian_row is not None and _as_float(asian_row.get("handicap")) is not None:
        quote["line"] = _as_float(asian_row.get("handicap"))
        quote["home_water"] = _as_float(asian_row.get("home_water"))
        quote["away_water"] = _as_float(asian_row.get("away_water"))
        quote["line_source"] = "odds_asian"
        return quote
    snap = _pick_snapshot(
        snapshot_rows, _AH_MARKETS, ready=lambda row: _as_float(row.get("line")) is not None,
    )
    if snap is not None and _as_float(snap.get("line")) is not None:
        quote["line"] = snapshot_asian_line_to_warehouse(float(snap["line"]))
        quote["home_water"] = _as_float(snap.get("water_home"))
        quote["away_water"] = _as_float(snap.get("water_away"))
        quote["line_source"] = "odds_snapshot"
    return quote


def choose_totals_close(table_row: Mapping[str, Any] | None, snapshot_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """一家机构的大小球临盘。进球线不取反。"""
    quote = _empty_quote()
    if table_row is not None and _as_float(table_row.get("line")) is not None:
        quote["line"] = _as_float(table_row.get("line"))
        quote["over_water"] = _as_float(table_row.get("over_water"))
        quote["under_water"] = _as_float(table_row.get("under_water"))
        quote["line_source"] = "odds_goalline"
        return quote
    snap = _pick_snapshot(
        snapshot_rows, _OU_MARKETS, ready=lambda row: _as_float(row.get("line")) is not None,
    )
    if snap is not None and _as_float(snap.get("line")) is not None:
        quote["line"] = _as_float(snap.get("line"))
        # 只取水位。欧式大小球赔率留在快照里，不写进水位列，避免两种报价混在一起。
        quote["over_water"] = _as_float(snap.get("water_over"))
        quote["under_water"] = _as_float(snap.get("water_under"))
        quote["line_source"] = "odds_snapshot"
    return quote


def choose_euro_close(home_only: Any, snapshot_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """一家机构的欧盘临盘。三项必须齐全；只有主胜的旧表不能单独结算。"""
    quote = _empty_quote()
    complete = _pick_snapshot(
        snapshot_rows,
        _EURO_MARKETS,
        ready=lambda row: all(_as_float(row.get(key)) is not None for key in ("price_home", "price_draw", "price_away")),
    )
    snap = complete or _pick_snapshot(
        snapshot_rows,
        _EURO_MARKETS,
        ready=lambda row: any(_as_float(row.get(key)) is not None for key in ("price_home", "price_draw", "price_away")),
    )
    if snap is not None:
        quote["odds_home"] = _as_float(snap.get("price_home"))
        quote["odds_draw"] = _as_float(snap.get("price_draw"))
        quote["odds_away"] = _as_float(snap.get("price_away"))
        quote["line_source"] = "odds_snapshot"
    home_value = _as_float(home_only)
    if quote["odds_home"] is None and home_value is not None:
        quote["odds_home"] = home_value
        quote["line_source"] = quote["line_source"] or "odds_euro_home"
    present = [quote["odds_home"], quote["odds_draw"], quote["odds_away"]]
    if any(value is None for value in present):
        # 见到了不完整的报价（包括只有主胜）时，原因是临盘三项不全，而不是完全没采到。
        quote["euro_incomplete"] = any(value is not None for value in present)
    return quote


def compute_match_records(
    match_id: int,
    home_goals: int,
    away_goals: int,
    quotes: Mapping[tuple[int, str, str], Mapping[str, Any]],
    *,
    computed_at: str,
) -> list[dict[str, Any]]:
    """一场比赛固定写出十五行：五家机构乘三个市场。缺临盘也写行，格子状态为无法获取。"""
    records: list[dict[str, Any]] = []
    total_goals = int(home_goals) + int(away_goals)
    for book in BOOKS:
        for market in MARKETS:
            quote = dict(quotes.get((match_id, book, market)) or _empty_quote())
            cell = _cell_from_quote(market, int(home_goals), int(away_goals), total_goals, quote)
            records.append(_record(match_id, book, market, cell, quote, int(home_goals), int(away_goals), computed_at))
    return records


def plan_backfill(
    conn: sqlite3.Connection,
    *,
    match_ids: Sequence[int] | None = None,
    jingcai_date: str | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """统计将要写入和删除的行。不创建表，也不修改数据库。"""
    scope = _scope_match_ids(conn, match_ids, jingcai_date)
    blocked = load_blocking_flags(conn)
    eligible = _eligible_scores(conn, scope, blocked)
    quotes = load_close_quotes(conn, [match_id for match_id, _home_goals, _away_goals in eligible])
    computed_at = format_computed_at(now)
    records: list[dict[str, Any]] = []
    for match_id, home_goals, away_goals in eligible:
        records.extend(compute_match_records(
            match_id, home_goals, away_goals, quotes, computed_at=computed_at,
        ))
    eligible_ids = {match_id for match_id, _home, _away in eligible}
    delete_ids = [match_id for match_id in scope if match_id not in eligible_ids]
    return {
        "records": records,
        "delete_match_ids": delete_ids,
        "summary": _summarize(records, eligible, blocked, scope, computed_at, applied=False),
    }


def apply_backfill(
    conn: sqlite3.Connection,
    *,
    match_ids: Sequence[int] | None = None,
    jingcai_date: str | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """覆盖写入 market_settle，并删除范围内不再可结算的场次。调用方负责提交。"""
    ensure_schema(conn)
    planned = plan_backfill(conn, match_ids=match_ids, jingcai_date=jingcai_date, now=now)
    _upsert_records(conn, planned["records"])
    deleted = _delete_matches(conn, planned["delete_match_ids"])
    summary = dict(planned["summary"])
    summary["applied"] = True
    summary["deleted_matches"] = deleted
    return summary


def format_computed_at(now: datetime | str | None = None) -> str:
    """计算时间写成带 +08:00 的时刻。传入字符串时原样使用，便于测试固定时间。"""
    if isinstance(now, str) and now:
        return now
    current = now if isinstance(now, datetime) else datetime.now(TZ_CN)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TZ_CN)
    else:
        current = current.astimezone(TZ_CN)
    return current.isoformat(timespec="seconds")


def format_plain_number(value: float) -> str:
    """与前端 formatPick 的数字写法对齐：整数不带小数点，其余去掉多余的尾随零。"""
    number = float(value)
    if not math.isfinite(number) or abs(number) < 1e-12:
        return "0"
    nearest = round(number)
    if abs(number - nearest) < 1e-9:
        return str(int(nearest))
    return f"{number:.10f}".rstrip("0").rstrip(".")


def format_signed_number(value: float) -> str:
    """正数带加号，负数带减号，零写成 0。减号用半角连字符，与 formatPick 一致。"""
    number = float(value)
    if abs(number) < 1e-12:
        return "0"
    if number > 0:
        return "+" + format_plain_number(number)
    return "-" + format_plain_number(-number)


def format_decimal_odds(value: float) -> str:
    """与前端 fmtNum 一致：先保留两位小数，再去掉由 Number() 吃掉的尾随零。"""
    rounded = float(f"{float(value):.2f}")
    return format_plain_number(rounded)


# ----------------------------------------------------------------------------- 内部


def _sign_sum_against_line(base: float, line: float) -> int:
    """复用 quarter_split 得到半盘，再对每个半盘的符号加一次。权重只用于结算码，不用于这里。"""
    total = 0
    for half_line, _weight in bt.quarter_split(float(line)):
        total += _margin_sign(base - half_line)
    return total


def _margin_sign(margin: float) -> int:
    # 与 backtest._half_outcome 的 1e-12 阈值一致，避免把浮点噪声当成赢盘。
    if margin > 1e-12:
        return 1
    if margin < -1e-12:
        return -1
    return 0


def _side_from_sign_sum(sign_sum: int, positive_side: str, negative_side: str) -> tuple[Optional[str], bool]:
    """符号和大于零指向正侧，小于零指向负侧。赢半仍然指向赢的一侧，只有和为零才是走水。"""
    if sign_sum > 0:
        return positive_side, False
    if sign_sum < 0:
        return negative_side, False
    return None, True


def _cell_from_quote(market: str, home_goals: int, away_goals: int, total_goals: int, quote: Mapping[str, Any]) -> dict[str, Any]:
    if market == "ah":
        if quote.get("line") is None:
            return _line_cell(None, unavailable=True, reason="not_captured")
        return settle_asian(home_goals, away_goals, float(quote["line"]))
    if market == "ou":
        if quote.get("line") is None:
            return _line_cell(None, unavailable=True, reason="not_captured")
        return settle_totals(total_goals, float(quote["line"]))
    odds = (quote.get("odds_home"), quote.get("odds_draw"), quote.get("odds_away"))
    if any(value is None for value in odds):
        reason = "no_close_odds" if quote.get("euro_incomplete") else "not_captured"
        return _euro_cell(unavailable=True, reason=reason)
    return settle_1x2(home_goals, away_goals, float(odds[0]), float(odds[1]), float(odds[2]))


def _record(
    match_id: int,
    book: str,
    market: str,
    cell: Mapping[str, Any],
    quote: Mapping[str, Any],
    home_goals: int,
    away_goals: int,
    computed_at: str,
) -> dict[str, Any]:
    side = cell.get("side")
    if market == "x1x2":
        odds_used = cell.get("odds") if cell.get("cell_status") == "ok" else None
        line = None
        settle_code = None
        push = 0
    else:
        odds_used = _winning_water(market, side, quote) if cell.get("cell_status") == "ok" and not cell.get("push") else None
        line = cell.get("line")
        settle_code = cell.get("settle_code")
        push = 1 if cell.get("push") else 0
        if settle_code is not None and not isinstance(settle_code, str):
            raise TypeError("settle_code 列只能写入结算码字符串，不能写入整个二元组。")
    return {
        "match_id": match_id,
        "book": book,
        "market": market,
        "line": line,
        "home_water": quote.get("home_water"),
        "away_water": quote.get("away_water"),
        "over_water": quote.get("over_water"),
        "under_water": quote.get("under_water"),
        "odds_home": quote.get("odds_home"),
        "odds_draw": quote.get("odds_draw"),
        "odds_away": quote.get("odds_away"),
        "odds_used": odds_used,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "side": side,
        "push": push,
        "settle_code": settle_code,
        "display_text": cell.get("display_text"),
        "cell_status": cell.get("cell_status"),
        "null_reason": cell.get("null_reason"),
        "null_reason_zh": cell.get("null_reason_zh"),
        "line_source": quote.get("line_source"),
        "computed_at": computed_at,
    }


def _winning_water(market: str, side: str | None, quote: Mapping[str, Any]) -> float | None:
    if side is None:
        return None
    if market == "ah":
        return quote.get("home_water") if side == "home" else quote.get("away_water")
    if market == "ou":
        return quote.get("over_water") if side == "over" else quote.get("under_water")
    return None


def _rows_to_by_book(rows: Sequence[Any]) -> dict[str, dict[str, dict[str, Any]]]:
    shell = unavailable_by_book("not_captured")
    for row in rows:
        book = row["book"]
        market = row["market"]
        if book not in shell or market not in MARKETS:
            continue
        shell[book][market] = _row_to_cell(row)
    return shell


def _records_to_by_book(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    shell = unavailable_by_book("not_captured")
    for record in records:
        book = record["book"]
        market = record["market"]
        if book not in shell or market not in MARKETS:
            continue
        shell[book][market] = _record_to_cell(record)
    return shell


def _row_to_cell(row: Any) -> dict[str, Any]:
    reason = row["null_reason"]
    reason_zh = row["null_reason_zh"] or reason_codes.zh(reason)
    if row["market"] == "x1x2":
        return {
            "side": row["side"],
            "odds": row["odds_used"],
            "display_text": row["display_text"],
            "cell_status": row["cell_status"],
            "null_reason": reason,
            "null_reason_zh": reason_zh,
        }
    code = row["settle_code"]
    if code is not None and not isinstance(code, str):
        raise TypeError("market_settle.settle_code 必须是字符串或空值。")
    return {
        "line": row["line"],
        "side": row["side"],
        "push": bool(row["push"]),
        "settle_code": code,
        "display_text": row["display_text"],
        "cell_status": row["cell_status"],
        "null_reason": reason,
        "null_reason_zh": reason_zh,
    }


def _record_to_cell(record: Mapping[str, Any]) -> dict[str, Any]:
    if record["market"] == "x1x2":
        return {
            "side": record.get("side"),
            "odds": record.get("odds_used") if record.get("cell_status") == "ok" else None,
            "display_text": record.get("display_text"),
            "cell_status": record.get("cell_status"),
            "null_reason": record.get("null_reason"),
            "null_reason_zh": record.get("null_reason_zh"),
        }
    return {
        "line": record.get("line"),
        "side": record.get("side"),
        "push": bool(record.get("push")),
        "settle_code": record.get("settle_code"),
        "display_text": record.get("display_text"),
        "cell_status": record.get("cell_status"),
        "null_reason": record.get("null_reason"),
        "null_reason_zh": record.get("null_reason_zh"),
    }


def _line_cell(
    line: float | None,
    *,
    unavailable: bool = False,
    reason: str | None = None,
    side: str | None = None,
    push: bool = False,
    settle_code: str | None = None,
    display_text: str | None = None,
) -> dict[str, Any]:
    if unavailable:
        return {
            "line": line,
            "side": None,
            "push": False,
            "settle_code": None,
            "display_text": None,
            "cell_status": "unavailable",
            "null_reason": reason,
            "null_reason_zh": reason_codes.zh(reason),
        }
    if settle_code is not None and not isinstance(settle_code, str):
        raise TypeError("settle_code 列只能写入结算码字符串，不能写入整个二元组。")
    return {
        "line": line,
        "side": side,
        "push": bool(push),
        "settle_code": settle_code,
        "display_text": display_text,
        "cell_status": "ok",
        "null_reason": None,
        "null_reason_zh": None,
    }


def _euro_cell(*, unavailable: bool = False, reason: str | None = None) -> dict[str, Any]:
    return {
        "side": None,
        "odds": None,
        "display_text": None,
        "cell_status": "unavailable" if unavailable else "ok",
        "null_reason": reason,
        "null_reason_zh": reason_codes.zh(reason),
    }


def _empty_line_cell() -> dict[str, Any]:
    return {key: (False if key == "push" else None) for key in _LINE_CELL_KEYS}


def _empty_euro_cell() -> dict[str, Any]:
    return {key: None for key in _EURO_CELL_KEYS}


def _empty_quote() -> dict[str, Any]:
    return {
        "line": None,
        "home_water": None,
        "away_water": None,
        "over_water": None,
        "under_water": None,
        "odds_home": None,
        "odds_draw": None,
        "odds_away": None,
        "line_source": None,
        "euro_incomplete": False,
    }


def _format_asian_display(side: str | None, line: float) -> str | None:
    """下主队时盘口用仓库数值带符号；下客队时取相反数。与 formatPick 的亚盘分支一致。"""
    if side == "home":
        return "主" + format_signed_number(line)
    if side == "away":
        return "客" + format_signed_number(-float(line))
    return None


def _format_totals_display(side: str | None, line: float) -> str | None:
    """大小球短文案是盘口在前、大或小在后，例如「2.25大」。"""
    if side == "over":
        return format_plain_number(line) + "大"
    if side == "under":
        return format_plain_number(line) + "小"
    return None


def _pick_snapshot(
    rows: Sequence[Mapping[str, Any]],
    markets: Sequence[str],
    *,
    ready: Any = None,
) -> Mapping[str, Any] | None:
    """同一机构内挑选规则临盘。先比通道，再比市场代码，不看其他机构。"""
    market_rank = {name: index for index, name in enumerate(markets)}
    best: Mapping[str, Any] | None = None
    best_key: tuple[int, int] | None = None
    for row in rows:
        market = row.get("market")
        if market not in market_rank:
            continue
        channel = str(row.get("channel") or "")
        if channel not in _CHANNEL_RANK:
            continue
        if ready is not None and not ready(row):
            continue
        key = (_CHANNEL_RANK[channel], market_rank[str(market)])
        if best_key is None or key < best_key:
            best = row
            best_key = key
    return best


def _load_asian_close(conn: sqlite3.Connection, match_ids: Sequence[int]) -> dict[tuple[int, str], dict[str, Any]]:
    if not _has_table(conn, "odds_asian"):
        return {}
    found: dict[tuple[int, str], dict[str, Any]] = {}
    for chunk in _chunks(match_ids):
        placeholders = ",".join("?" for _ in chunk)
        book_placeholders = ",".join("?" for _ in BOOKS)
        sql = (
            "SELECT match_id, book, handicap, home_water, away_water FROM odds_asian "
            f"WHERE phase = 'close' AND match_id IN ({placeholders}) AND book IN ({book_placeholders})"
        )
        for row in conn.execute(sql, tuple(chunk) + BOOKS):
            found[(int(row["match_id"]), row["book"])] = {
                "handicap": row["handicap"],
                "home_water": row["home_water"],
                "away_water": row["away_water"],
            }
    return found


def _load_totals_table(conn: sqlite3.Connection, match_ids: Sequence[int]) -> dict[tuple[int, str], dict[str, Any]]:
    """若库里以后有 odds_goalline，优先用它。当前仓库没有这张表时直接跳过。"""
    if not _has_table(conn, "odds_goalline"):
        return {}
    columns = _column_names(conn, "odds_goalline")
    if "line" not in columns or "book" not in columns or "match_id" not in columns:
        return {}
    over_name = "over_water" if "over_water" in columns else ("water_over" if "water_over" in columns else None)
    under_name = "under_water" if "under_water" in columns else ("water_under" if "water_under" in columns else None)
    phase_filter = "AND phase = 'close'" if "phase" in columns else ""
    selected = ["match_id", "book", "line"]
    if over_name:
        selected.append(over_name)
    if under_name:
        selected.append(under_name)
    found: dict[tuple[int, str], dict[str, Any]] = {}
    for chunk in _chunks(match_ids):
        placeholders = ",".join("?" for _ in chunk)
        book_placeholders = ",".join("?" for _ in BOOKS)
        sql = (
            f"SELECT {', '.join(selected)} FROM odds_goalline "
            f"WHERE match_id IN ({placeholders}) AND book IN ({book_placeholders}) {phase_filter}"
        )
        for row in conn.execute(sql, tuple(chunk) + BOOKS):
            found[(int(row["match_id"]), row["book"])] = {
                "line": row["line"],
                "over_water": row[over_name] if over_name else None,
                "under_water": row[under_name] if under_name else None,
            }
    return found


def _load_euro_home_only(conn: sqlite3.Connection, match_ids: Sequence[int]) -> dict[tuple[int, str], Any]:
    if not _has_table(conn, "odds_euro_home"):
        return {}
    found: dict[tuple[int, str], Any] = {}
    for chunk in _chunks(match_ids):
        placeholders = ",".join("?" for _ in chunk)
        book_placeholders = ",".join("?" for _ in BOOKS)
        sql = (
            "SELECT match_id, book, home_win FROM odds_euro_home "
            f"WHERE phase = 'close' AND match_id IN ({placeholders}) AND book IN ({book_placeholders})"
        )
        for row in conn.execute(sql, tuple(chunk) + BOOKS):
            found[(int(row["match_id"]), row["book"])] = row["home_win"]
    return found


def _load_close_snapshots(conn: sqlite3.Connection, match_ids: Sequence[int]) -> dict[tuple[int, str], list[dict[str, Any]]]:
    if not _has_table(conn, "odds_snapshot"):
        return {}
    found: dict[tuple[int, str], list[dict[str, Any]]] = {}
    channels = CLOSE_CHANNELS
    for chunk in _chunks(match_ids):
        placeholders = ",".join("?" for _ in chunk)
        book_placeholders = ",".join("?" for _ in BOOKS)
        channel_placeholders = ",".join("?" for _ in channels)
        sql = (
            "SELECT match_id, book, market, channel, line, "
            "price_home, price_draw, price_away, price_over, price_under, "
            "water_home, water_away, water_over, water_under "
            "FROM odds_snapshot "
            f"WHERE point = 'close' AND match_id IN ({placeholders}) "
            f"AND book IN ({book_placeholders}) AND channel IN ({channel_placeholders})"
        )
        for row in conn.execute(sql, tuple(chunk) + BOOKS + channels):
            item = {key: row[key] for key in row.keys()}
            found.setdefault((int(row["match_id"]), row["book"]), []).append(item)
    return found


def _scope_match_ids(
    conn: sqlite3.Connection,
    match_ids: Sequence[int] | None,
    jingcai_date: str | None,
) -> list[int]:
    if match_ids is not None:
        return [int(match_id) for match_id in match_ids]
    if jingcai_date:
        rows = conn.execute("SELECT id FROM matches WHERE jingcai_date = ? ORDER BY id", (jingcai_date,))
    else:
        rows = conn.execute("SELECT id FROM matches ORDER BY id")
    return [int(row[0]) for row in rows]


def _eligible_scores(
    conn: sqlite3.Connection,
    scope: Sequence[int],
    blocked: Mapping[int, str],
) -> list[tuple[int, int, int]]:
    if not scope or not _has_table(conn, "results"):
        return []
    scope_set = set(scope)
    found: list[tuple[int, int, int]] = []
    for chunk in _chunks(scope):
        placeholders = ",".join("?" for _ in chunk)
        sql = (
            "SELECT match_id, home_goals, away_goals FROM results "
            f"WHERE match_id IN ({placeholders}) AND home_goals IS NOT NULL AND away_goals IS NOT NULL"
        )
        for row in conn.execute(sql, tuple(chunk)):
            match_id = int(row["match_id"])
            if match_id not in scope_set or match_id in blocked:
                continue
            found.append((match_id, int(row["home_goals"]), int(row["away_goals"])))
    return found


def _summarize(
    records: Sequence[Mapping[str, Any]],
    eligible: Sequence[tuple[int, int, int]],
    blocked: Mapping[int, str],
    scope: Sequence[int],
    computed_at: str,
    *,
    applied: bool,
) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for record in records:
        status = str(record.get("cell_status") or "")
        by_status[status] = by_status.get(status, 0) + 1
        reason = record.get("null_reason")
        if reason:
            by_reason[str(reason)] = by_reason.get(str(reason), 0) + 1
    blocked_in_scope = {match_id: flag for match_id, flag in blocked.items() if match_id in set(scope)}
    return {
        "applied": applied,
        "computed_at": computed_at,
        "scope_matches": len(scope),
        "eligible_matches": len(eligible),
        "blocked_matches": len(blocked_in_scope),
        "blocked_flags": dict(sorted(blocked_in_scope.items())),
        "rows": len(records),
        "by_cell_status": by_status,
        "by_null_reason": by_reason,
    }


def _upsert_records(conn: sqlite3.Connection, records: Sequence[Mapping[str, Any]]) -> None:
    if not records:
        return
    assignments = ", ".join(
        f"{column}=excluded.{column}"
        for column in _RECORD_COLUMNS
        if column not in ("match_id", "book", "market")
    )
    sql = (
        f"INSERT INTO market_settle ({', '.join(_RECORD_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in _RECORD_COLUMNS)}) "
        f"ON CONFLICT(match_id, book, market) DO UPDATE SET {assignments}"
    )
    conn.executemany(sql, [tuple(record[column] for column in _RECORD_COLUMNS) for record in records])


def _delete_matches(conn: sqlite3.Connection, match_ids: Sequence[int]) -> int:
    if not match_ids or not _has_table(conn, "market_settle"):
        return 0
    deleted = 0
    for chunk in _chunks(match_ids):
        placeholders = ",".join("?" for _ in chunk)
        before = conn.total_changes
        conn.execute(f"DELETE FROM market_settle WHERE match_id IN ({placeholders})", tuple(chunk))
        deleted += conn.total_changes - before
    return deleted


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _chunks(values: Iterable[int], size: int = 400) -> Iterable[list[int]]:
    batch: list[int] = []
    for value in values:
        batch.append(int(value))
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number == 0.0:
        return 0.0
    return number
