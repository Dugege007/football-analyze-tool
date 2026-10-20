"""人气差值：临盘欧赔隐含概率差。

库内已有小数时原样使用（含历史临盘结果）。空值按临盘欧赔现算：
至少两家完整胜平负时，对三项赔率做算术平均后再取主胜隐含概率减客胜隐含概率；
不足两家时按平博、威廉、澳门、皇冠、Bet365 取第一家完整临盘。
只认完整三项。不读雷速，也不用只有主胜的旧表。
"""
from __future__ import annotations

from typing import Any, Optional

# 与多家平均同一批机构。顺序只用于「不足两家」时的回退，不参与平均的排序展示。
BOOKS: tuple[str, ...] = ("macau", "crown", "pinnacle", "william", "bet365")
FALLBACK_ORDER: tuple[str, ...] = ("pinnacle", "william", "macau", "crown", "bet365")
MIN_BOOKS = 2
SOURCE_AVG = "close_euro_avg"


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def diff_from_1x2(home: Any, draw: Any, away: Any) -> Optional[float]:
    """主胜隐含概率减客胜隐含概率。赔率须大于 1。与欧赔去水 p_i=(1/o_i)/Σ(1/o) 相同。"""
    prices = [_finite(home), _finite(draw), _finite(away)]
    if any(price is None or price <= 1.0 for price in prices):
        return None
    inv = [1.0 / price for price in prices]  # type: ignore[operator]
    total = sum(inv)
    if total <= 0:
        return None
    return round(inv[0] / total - inv[2] / total, 6)


def _triple(cell: Any) -> Optional[tuple[float, float, float]]:
    """完整且状态为 ok 的胜平负。半套主胜、被截止时刻隐藏的格子都不用。"""
    if not isinstance(cell, dict) or cell.get("cell_status") != "ok":
        return None
    home, draw, away = _finite(cell.get("home")), _finite(cell.get("draw")), _finite(cell.get("away"))
    if home is None or draw is None or away is None or home <= 1 or draw <= 1 or away <= 1:
        return None
    return home, draw, away


def popularity_from_prices(prices: dict[str, tuple[Any, Any, Any]]) -> Optional[dict[str, Any]]:
    """由各家完整临盘赔率计算。供补数脚本与单测使用，不读数据库。"""
    complete: dict[str, tuple[float, float, float]] = {}
    for book in BOOKS:
        triple = prices.get(book)
        if not triple or len(triple) != 3:
            continue
        value = diff_from_1x2(*triple)
        if value is None:
            continue
        home, draw, away = (_finite(triple[0]), _finite(triple[1]), _finite(triple[2]))
        if home is None or draw is None or away is None:
            continue
        complete[book] = (home, draw, away)
    if len(complete) >= MIN_BOOKS:
        books = [book for book in BOOKS if book in complete]
        mean = tuple(sum(complete[book][i] for book in books) / len(books) for i in range(3))
        value = diff_from_1x2(*mean)
        if value is not None:
            return {"value": value, "source": SOURCE_AVG, "books": books, "as_of": None}
    for book in FALLBACK_ORDER:
        if book not in complete:
            continue
        value = diff_from_1x2(*complete[book])
        if value is None:
            continue
        return {"value": value, "source": f"close_euro_{book}", "books": [book], "as_of": None}
    return None


def _as_of_from_avg(cell: dict) -> Optional[str]:
    detail = cell.get("avg_detail") if isinstance(cell.get("avg_detail"), dict) else {}
    sources = detail.get("sources") if isinstance(detail.get("sources"), dict) else {}
    times = [
        row.get("recorded_at")
        for row in sources.values()
        if isinstance(row, dict) and isinstance(row.get("recorded_at"), str) and row.get("recorded_at")
    ]
    return max(times) if times else None


def popularity_from_close_blocks(blocks: dict | None) -> Optional[dict[str, Any]]:
    """用数据表已经组装好的临盘欧赔格子。多家平均优先，否则按回退顺序取第一家。"""
    if not isinstance(blocks, dict):
        return None
    avg = blocks.get("avg")
    avg_close = avg.get("close") if isinstance(avg, dict) else None
    triple = _triple(avg_close)
    if triple is not None:
        value = diff_from_1x2(*triple)
        if value is not None:
            return {
                "value": value,
                "source": SOURCE_AVG,
                "books": list((avg_close.get("avg_detail") or {}).get("averaged_books") or []),
                "as_of": _as_of_from_avg(avg_close) if isinstance(avg_close, dict) else None,
            }
    for book in FALLBACK_ORDER:
        block = blocks.get(book)
        close = block.get("close") if isinstance(block, dict) else None
        triple = _triple(close)
        if triple is None:
            continue
        value = diff_from_1x2(*triple)
        if value is None:
            continue
        recorded = close.get("recorded_at") if isinstance(close, dict) else None
        return {
            "value": value,
            "source": f"close_euro_{book}",
            "books": [book],
            "as_of": recorded if isinstance(recorded, str) else None,
        }
    return None


def _stored_diff(value: Any) -> Optional[float]:
    number = _finite(value)
    return number


def project_table_stats(stats: dict | None, x_blocks: dict | None) -> dict[str, Any]:
    """宽表一行的 stats。已有人气差值保持不动；空值用当前响应里可见的临盘欧赔补上。不写库。"""
    src = stats if isinstance(stats, dict) else {}
    out: dict[str, Any] = {k: v for k, v in src.items() if k != "support_proxy_odds"}
    out.setdefault("recent", None)
    out.setdefault("h2h", None)
    if not isinstance(out.get("rank"), dict):
        out["rank"] = {"home": None, "away": None}
    out.setdefault("injury", None)
    out.setdefault("weather", None)
    if not isinstance(out.get("meta"), dict):
        out["meta"] = {}
    if not isinstance(out.get("obs"), dict):
        out["obs"] = {}
    stored = _stored_diff(out.get("popularity_diff"))
    if stored is not None:
        out["popularity_diff"] = stored
        return out
    computed = popularity_from_close_blocks(x_blocks)
    if computed is None:
        out["popularity_diff"] = None
        return out
    out["popularity_diff"] = computed["value"]
    meta = dict(out.get("meta") or {})
    meta["popularity"] = {"source": computed["source"], "as_of": computed["as_of"]}
    out["meta"] = meta
    return out
