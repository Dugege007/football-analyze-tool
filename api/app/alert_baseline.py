"""亚盘预警档位（alert）的基准：按每一格自己的抓取时刻固定（分析师、算法顾问 2026-10-14 决定）。

规则：
- 只给亚盘中盘和临盘算预警，初盘和欧赔不在这里算。
- 一格的基准样本只取同一盘种、同一机构、同一阶段的其他场次格子，并且这些格子的记录时间严格早于这一格的抓取时刻；
  同一场比赛自己的格子一律不进基准。
- 样本数少于门槛（默认 100，可用环境变量 APP_ALERT_MIN_N 改）时，这一格的预警为空值。
- 每一格实际用的样本数写进 alert_sample_n。
- 不写任何库，也不落盘缓存；结果只由抓取时刻之前的数据决定，什么时候重算都一样。
"""
from __future__ import annotations

import bisect
import os
from datetime import datetime
from typing import Callable, Optional

ALERT_PHASES: tuple[str, ...] = ("mid", "close")
DEFAULT_ALERT_MIN_N = 100


def alert_min_n() -> int:
    """预警样本数门槛；环境变量 APP_ALERT_MIN_N 可覆盖，读不出合法正整数时用 100。"""
    raw = os.environ.get("APP_ALERT_MIN_N")
    try:
        v = int(raw) if raw not in (None, "") else DEFAULT_ALERT_MIN_N
    except ValueError:
        return DEFAULT_ALERT_MIN_N
    return v if v > 0 else DEFAULT_ALERT_MIN_N


def _quantile(sample: list[float], q: float) -> Optional[float]:
    n = len(sample)
    if n == 0:
        return None
    pos = (n - 1) * q
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sample[lo] + (sample[hi] - sample[lo]) * (pos - lo)


class AsOfCellBaseline:
    """按记录时刻排序的样本；查询时取严格早于抓取时刻、且不属于同一场的样本，算分位数。"""

    def __init__(self, qs: dict[str, float]):
        self.qs = dict(qs)
        self._raw: dict[tuple, dict[int, tuple[datetime, float]]] = {}
        self._sorted: dict[tuple, tuple[list[datetime], list[int], list[float]]] = {}

    def add(self, key: tuple, match_pk: int, recorded_at: Optional[datetime], value: Optional[float]) -> None:
        if value is None or recorded_at is None:
            return
        self._raw.setdefault(key, {})[int(match_pk)] = (recorded_at, float(value))

    def freeze(self) -> None:
        for k, v in self._raw.items():
            trip = sorted(((ts, pk, val) for pk, (ts, val) in v.items()), key=lambda t: (t[0], t[1]))
            self._sorted[k] = ([t[0] for t in trip], [t[1] for t in trip], [t[2] for t in trip])

    def sample(self, key: tuple, match_pk: int, at: datetime) -> list[float]:
        ts, pks, vals = self._sorted.get(key, ([], [], []))
        hi = bisect.bisect_left(ts, at)  # 严格早于抓取时刻
        return sorted(v for pk, v in zip(pks[:hi], vals[:hi]) if pk != int(match_pk))

    def get(self, key: tuple, match_pk: int, at: Optional[datetime]) -> tuple[dict[str, Optional[float]], int]:
        if at is None:
            return {name: None for name in self.qs}, 0
        s = self.sample(key, match_pk, at)
        return {name: _quantile(s, q) for name, q in self.qs.items()}, len(s)


def apply_alert_level(cell: dict, key: tuple, match_pk: int, base: AsOfCellBaseline,
                      level_fn: Callable, min_n: int, parse_ts: Callable) -> None:
    """给一格写 alert_level 与 alert_sample_n。没有抓取时刻 / 没有返还率的格子样本数记 0、档位为空值。"""
    at = parse_ts(cell.get("recorded_at"))
    qs, n = base.get(key, match_pk, at)
    cell["alert_sample_n"] = n
    cell["alert_level"] = level_fn(cell.get("return_rate"), cell.get("water_source"), qs, n >= min_n)
