"""预测、回测、切片的结算盘解析。

2026-10-16 用户与分析师决议：未显式指定结算盘时，平博临盘优先，没有平博临盘再用澳门临盘。
显式 ``macau_close``（以及任意单一 ``book_phase``）只结算该庄，不回退。

机构赛果 ``market_settle`` 不使用本模块。本模块不写数据库，也不改 ``predictions.settle_book``。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

# 请求、策略模板、预测行缺省时使用的令牌。
DEFAULT_SETTLE_BOOK = "pinnacle_then_macau"

# 优先级写死：平博临盘 > 澳门临盘。
SETTLE_BOOK_PRIORITY: tuple[str, ...] = ("pinnacle_close", "macau_close")

# 与水位 SETTLEMENT_VERSION 正交。默认结算盘换代时递增，并进入优先级回测的 run 指纹。
SETTLEMENT_BOOK_VERSION = "settle_book_pinnacle_then_macau_v1"

# 与 DEFAULT_SETTLE_BOOK 等价的写法，便于和口径式 pinnacle_close > macau_close 对齐。
_PRIORITY_ALIASES = frozenset({
    DEFAULT_SETTLE_BOOK,
    "pinnacle_close>macau_close",
    "pinnacle_close>macau",
})


@dataclass(frozen=True)
class SettleSpec:
    """一次结算盘请求。priority 为真时按 SETTLE_BOOK_PRIORITY 逐级取临盘。"""

    token: str
    priority: bool
    book: str | None
    phase: str | None


@dataclass(frozen=True)
class SettleQuote:
    """一场比赛解析后的结算盘。

    fallback_book 仅在发生回退时为实际用到的庄（当前为 macau）；未回退则为 None。
    settled_with 为实际用到的庄代码（pinnacle / macau），没有可用临盘时为 None。
    """

    requested: str
    priority: bool
    book: str | None
    phase: str | None
    settled_with: str | None
    fallback_book: str | None
    handicap: float | None
    home_water: Any
    away_water: Any
    water_src: Any
    censored_home: bool
    censored_away: bool


def parse_settle_spec(settle_book: str | None) -> SettleSpec:
    """空值走默认优先级。单一 book_phase 不回退。

    无法识别、且不含下划线的形态回落澳门临盘，与旧 ``_parse_settle_book`` 一致。
    """
    raw = (settle_book or "").strip().lower()
    if raw == "" or raw in _PRIORITY_ALIASES:
        return SettleSpec(token=DEFAULT_SETTLE_BOOK, priority=True, book=None, phase=None)
    book, phase = _split_single(raw)
    return SettleSpec(token=raw, priority=False, book=book, phase=phase)


def _split_single(token: str) -> tuple[str, str]:
    """macau_close → (macau, close)。与旧解析相同：有下划线就从右侧拆开。"""
    if "_" in token and ">" not in token:
        book, phase = token.rsplit("_", 1)
        if book and phase:
            return book, phase
    return "macau", "close"


def priority_steps() -> tuple[tuple[str, str, str], ...]:
    """(令牌, book, phase)，顺序即优先级。"""
    steps: list[tuple[str, str, str]] = []
    for token in SETTLE_BOOK_PRIORITY:
        book, phase = _split_single(token)
        steps.append((token, book, phase))
    return tuple(steps)


def _cell(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        keys = row.keys()
    except AttributeError:
        return None
    if key not in keys:
        return None
    return row[key]


def _usable(row: Any) -> bool:
    """有 handicap 才算有临盘。0 是平手盘，仍然可用。"""
    return _cell(row, "handicap") is not None


def resolve_settle_quote(getter: Callable[[str, str], Any], settle_book: str | None) -> SettleQuote:
    """getter(book, phase) 返回该庄该阶段的亚盘行；没有行则返回 None。

    平博行存在但 handicap 为空时，默认优先级继续看澳门。
    显式单庄没有临盘时不改用另一家。
    """
    spec = parse_settle_spec(settle_book)
    if spec.priority:
        steps = priority_steps()
        for index, (_token, book, phase) in enumerate(steps):
            row = getter(book, phase)
            if _usable(row):
                fallback = book if index > 0 else None
                return _from_row(spec, book, phase, row, fallback)
        return _empty(spec)
    book = spec.book or "macau"
    phase = spec.phase or "close"
    row = getter(book, phase)
    if not _usable(row):
        return _empty(spec)
    return _from_row(spec, book, phase, row, None)


def _empty(spec: SettleSpec) -> SettleQuote:
    return SettleQuote(
        requested=spec.token,
        priority=spec.priority,
        book=None,
        phase=None,
        settled_with=None,
        fallback_book=None,
        handicap=None,
        home_water=None,
        away_water=None,
        water_src=None,
        censored_home=False,
        censored_away=False,
    )


def _from_row(spec: SettleSpec, book: str, phase: str, row: Any, fallback: str | None) -> SettleQuote:
    raw = _cell(row, "handicap")
    try:
        handicap = float(raw)
    except (TypeError, ValueError):
        handicap = None
    return SettleQuote(
        requested=spec.token,
        priority=spec.priority,
        book=book,
        phase=phase,
        settled_with=book,
        fallback_book=fallback,
        handicap=handicap,
        home_water=_cell(row, "home_water"),
        away_water=_cell(row, "away_water"),
        water_src=_cell(row, "water_src"),
        censored_home=bool(_cell(row, "censored_home")),
        censored_away=bool(_cell(row, "censored_away")),
    )


def policy_fields(settle_book: str | None) -> dict[str, Any]:
    """回测摘要与 compare 顶层回显。单庄时两项为 None。"""
    spec = parse_settle_spec(settle_book)
    if not spec.priority:
        return {"settle_book_priority": None, "settlement_book_version": None}
    return {
        "settle_book_priority": list(SETTLE_BOOK_PRIORITY),
        "settlement_book_version": SETTLEMENT_BOOK_VERSION,
    }


def is_priority(settle_book: str | None) -> bool:
    return parse_settle_spec(settle_book).priority
