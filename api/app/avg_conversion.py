"""多家平均盘口换算的纯数学模块（接口约定第 3 版第 7 节）。

本模块只做计算，不读数据库，方便单独测试。函数与算法顾问的参考实现一一对应：
- 参考实现一：/workspace/odds-data/research/avg-line-conversion-2026-10-14/report/analyze.py
  中的 devig、revig、convert、q_side、halves（只用其中的幂次法）。
- 参考实现二：同目录 testcases/engine.py 中的 compute（选多数盘口、平票、剔除、平均）。

术语说明：
- 香港盘水位：不含本金的赔率，例如 0.92；隐含概率等于 1 除以（1 加水位）。
- 幂次法去水：找一个指数 k，使两边隐含概率各自取 k 次方后相加等于 1，得到公平概率。
- 泊松模型：主队、客队进球各自服从泊松分布且相互独立。
- 亚盘盘口一律用接口写法：主队让球为正，例如主让半球为 0.5。
- 一档等于 0.25 球。
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Optional

import numpy as np
from scipy import optimize
from scipy.stats import poisson

# 参与多家平均的五家机构，顺序即输出顺序。
BOOKS: tuple[str, ...] = ("macau", "crown", "pinnacle", "william", "bet365")
# 平票且中位数也分不开时，依次取这几家机构的盘口（分析师 2026-10-14 确定）。
TIE_ORDER: tuple[str, ...] = ("macau", "pinnacle", "crown", "bet365")
# 参与平均的机构少于这个家数时，这一格为数据不足（数值为空值，cell_status=insufficient，原因 too_few_books）。
# 2026-10-14 用户把门槛从至少三家改为至少两家；亚盘、大小球、欧赔的平均列共用这个常量。人气差值不用这个常量，暂不改。
MIN_BOOKS = 2
# 盘口与多数盘口相差这么多档或以上时，这家机构被剔除。
EXCLUDE_STEPS = 3
# 没有大小球盘口可用时，亚盘换算使用的总进球期望。
DEFAULT_GOAL_EXPECTATION = 2.6
# 每队进球数的截断上限（含）。算法顾问在群里说明为每队最多 10 球，测试用例参考实现也是如此，并且截断后重新归一。
MAX_GOALS = 10
DEVIG_METHOD = "power"
CONVERSION_MODEL = "poisson"
LEGACY_METHOD = "power_poisson"


def _goal_axis(max_goals: int) -> np.ndarray:
    return np.arange(0, max_goals + 1)


def score_grid(home_rate: float, away_rate: float, max_goals: int = MAX_GOALS,
               normalize: bool = True) -> np.ndarray:
    """返回比分概率矩阵，行是主队进球数，列是客队进球数。"""
    g = _goal_axis(max_goals)
    p = np.outer(poisson.pmf(g, home_rate), poisson.pmf(g, away_rate))
    return p / p.sum() if normalize else p


def halves(line: float) -> list[float]:
    """四分之一盘拆成两个相邻的半盘（或整球盘）；整球盘和半球盘原样返回。"""
    if abs(line * 2 - round(line * 2)) < 1e-9:
        return [line]
    return [line - 0.25, line + 0.25]


def q_side(grid: np.ndarray, outcome: np.ndarray, line: float) -> float:
    """返回上侧（亚盘为主队，大小球为大球）在这个盘口上的公平有效概率。

    outcome 与 grid 同形状，亚盘为主队净胜球，大小球为总进球。走水的部分不计入分子和分母。
    四分之一盘的两个半注各占一半本金，公平概率为两半赢的概率之和除以两半赢与输的概率之和。
    """
    num = 0.0
    den = 0.0
    for h in halves(line):
        x = outcome - h
        win = float(grid[x > 0].sum())
        lose = float(grid[x < 0].sum())
        num += win
        den += win + lose
    return num / den


def margin(w1: float, w2: float) -> float:
    """两项水位（香港盘写法）的利润率，即隐含概率之和减 1。"""
    return 1.0 / (1.0 + w1) + 1.0 / (1.0 + w2) - 1.0


def margin_1x2(odds: list[float]) -> float:
    """欧赔三项（含本金的小数赔率）合计的利润率。"""
    return sum(1.0 / o for o in odds) - 1.0


def devig(w1: float, w2: float) -> tuple[np.ndarray, float]:
    """幂次法去水。返回 [上侧公平概率, 下侧公平概率] 和指数 k。"""
    pi = np.array([1.0 / (1.0 + w1), 1.0 / (1.0 + w2)])
    k = optimize.brentq(lambda kk: float((pi ** kk).sum()) - 1.0, 0.3, 5.0)
    return pi ** k, k


def revig(probs: list[float], k: float) -> list[float]:
    """幂次法还原：用这家机构自己的指数 k 把公平概率还原成香港盘水位。"""
    return [float(1.0 / (p ** (1.0 / k)) - 1.0) for p in probs]


def _ah_fair_prob(diff_strength: float, mu: float, line: float, max_goals: int, normalize: bool) -> float:
    g = _goal_axis(max_goals)
    diff = np.subtract.outer(g, g)
    grid = score_grid(max((mu + diff_strength) / 2, 0.02), max((mu - diff_strength) / 2, 0.02),
                      max_goals, normalize)
    return q_side(grid, diff, line)


def _ou_fair_prob(total_rate: float, line: float, max_goals: int, normalize: bool) -> float:
    g = _goal_axis(max_goals)
    tot = np.add.outer(g, g)
    grid = score_grid(total_rate / 2, total_rate / 2, max_goals, normalize)
    return q_side(grid, tot, line)


def convert(market: str, w1: float, w2: float, line_from: float, line_to: float,
            mu: Optional[float] = None, *, max_goals: int = MAX_GOALS, normalize: bool = True
            ) -> Optional[tuple[list[float], float]]:
    """把一家机构在 line_from 上的水位换算到 line_to 上。

    market 为 "ah"（亚盘，盘口为接口写法，主队让球为正）或 "ou"（大小球）。
    mu 为亚盘换算用的总进球期望，大小球不用。
    返回（换算后水位 [上, 下]，上侧公平概率）；模型无解时返回 None。
    盘口相同时水位原样返回。
    """
    p, k = devig(w1, w2)
    if abs(line_from - line_to) < 1e-9:
        return [float(w1), float(w2)], float(p[0])
    try:
        if market == "ah":
            m = DEFAULT_GOAL_EXPECTATION if mu is None else float(mu)
            f = lambda d: _ah_fair_prob(d, m, line_from, max_goals, normalize) - p[0]  # noqa: E731
            if f(-5.0) * f(5.0) > 0:
                return None
            d = optimize.brentq(f, -5.0, 5.0, xtol=1e-10)
            q = _ah_fair_prob(d, m, line_to, max_goals, normalize)
        elif market == "ou":
            f = lambda t: _ou_fair_prob(t, line_from, max_goals, normalize) - p[0]  # noqa: E731
            if f(0.2) * f(9.0) > 0:
                return None
            t = optimize.brentq(f, 0.2, 9.0, xtol=1e-10)
            q = _ou_fair_prob(t, line_to, max_goals, normalize)
        else:
            raise ValueError(f"不支持的盘种：{market}")
    except ValueError:
        if market not in ("ah", "ou"):
            raise
        return None
    return revig([q, 1.0 - q], k), float(q)


def steps_between(a: float, b: float) -> int:
    """两个盘口相差的档数（一档 0.25 球，取绝对值后取整）。"""
    return int(round(abs(a - b) / 0.25))


def _r(v: Optional[float], nd: int = 4) -> Optional[float]:
    return None if v is None else round(float(v), nd)


def pick_majority(lines_by_book: dict[str, float]) -> tuple[Optional[float], str, Optional[float]]:
    """按第 7.3 节第 2、3 步选多数盘口。

    lines_by_book 只含投票机构。返回（多数盘口, 平票处理方式 tie_break, 中位数 median_line）。
    没有平票时 median_line 为 None。
    """
    if not lines_by_book:
        return None, "none", None
    lines = list(lines_by_book.values())
    counts: dict[float, int] = {}
    for x in lines:
        counts[x] = counts.get(x, 0) + 1
    top = max(counts.values())
    cand = sorted(x for x, c in counts.items() if c == top)
    if len(cand) == 1:
        return cand[0], "none", None
    med = float(statistics.median(lines))
    dist = [abs(c - med) for c in cand]
    best = [c for c, dd in zip(cand, dist) if abs(dd - min(dist)) < 1e-9]
    if len(best) == 1:
        return best[0], "median", med
    for b in TIE_ORDER:
        if b in lines_by_book and lines_by_book[b] in best:
            return lines_by_book[b], b, med
    return min(best), "smaller_line", med


def compute_avg(market: str, quotes: dict[str, dict], missing: dict[str, str],
                mu: Optional[float] = None) -> dict:
    """计算一格多家平均，返回格子取值和完整的 avg_detail。

    quotes：{机构: {"line": 盘口, "water": [上, 下]}}，欧赔为 {"water": [胜, 平, 负]}。
    亚盘、大小球水位必须已是香港盘写法；亚盘盘口必须已是接口写法。
    missing：{机构: 没有数据的原因}。
    返回 {"values": 格子取值或 None, "status": "ok" 或 "insufficient", "avg_detail": {...}}。
    """
    have = {b: quotes[b] for b in BOOKS if b in quotes}
    missing_list = [{"book": b, "reason": missing[b]} for b in BOOKS if b in missing and b not in have]
    if market == "x1x2":
        conv = []
        for b, q in have.items():
            o = [float(x) for x in q["water"]]
            conv.append({"book": b, "original_line": None, "steps": 0, "original_water": o,
                         "converted_water": list(o), "margin": _r(margin_1x2(o))})
        detail = _detail(majority=None, tie_break="none", median=None, voting=[], conv=conv, excl=[],
                         missing_list=missing_list, model=None, legacy_converted=None)
        # 欧赔不去水也不换算，去水方法为空（接口约定第 3 版更新后的第 7.6 节）。
        detail["devig_method"] = None
        ok = len(conv) >= MIN_BOOKS
        vals = ({k: _r(float(np.mean([c["converted_water"][i] for c in conv])))
                 for i, k in enumerate(("home", "draw", "away"))} if ok else None)
        return {"values": vals, "status": "ok" if ok else "insufficient", "avg_detail": detail}

    voting = {b: float(q["line"]) for b, q in have.items() if not (market == "ou" and b == "william")}
    maj, tb, med = pick_majority(voting)
    conv: list[dict] = []
    excl: list[dict] = []
    legacy: list[dict] = []
    if maj is not None:
        for b, q in have.items():
            line = float(q["line"])
            w = [float(q["water"][0]), float(q["water"][1])]
            st = steps_between(line, maj)
            mg = _r(margin(*w))
            if st >= EXCLUDE_STEPS:
                excl.append({"book": b, "reason": "deviation_too_large", "steps": st,
                             "original_line": line, "original_water": w, "margin": mg})
                continue
            res = convert(market, w[0], w[1], line, maj, mu)
            if res is None:
                # 模型无解（极端水位）。约定没有这种情况的取值，暂按剔除处理并写明原因。
                excl.append({"book": b, "reason": "conversion_failed", "steps": st,
                             "original_line": line, "original_water": w, "margin": mg})
                continue
            cw = [_r(x) for x in res[0]]
            conv.append({"book": b, "original_line": line, "steps": st, "original_water": w,
                         "converted_water": cw, "margin": mg})
            if st > 0:
                legacy.append({"book": b, "raw_line": line, "raw": w, "converted": cw, "method": LEGACY_METHOD})
    # 投票机构为空时（只可能是大小球只有威廉希尔一家）没有多数盘口，不换算，这一格为数据不足。
    detail = _detail(majority=maj, tie_break=tb, median=med, voting=list(voting), conv=conv, excl=excl,
                     missing_list=missing_list, model=CONVERSION_MODEL, legacy_converted=legacy)
    ok = len(conv) >= MIN_BOOKS
    vals = None
    if ok:
        keys = ("home_water", "away_water") if market == "ah" else ("over", "under")
        vals = {"line": maj, keys[0]: _r(float(np.mean([c["converted_water"][0] for c in conv]))),
                keys[1]: _r(float(np.mean([c["converted_water"][1] for c in conv])))}
    return {"values": vals, "status": "ok" if ok else "insufficient", "avg_detail": detail}


def _detail(*, majority, tie_break, median, voting, conv, excl, missing_list, model, legacy_converted) -> dict:
    averaged = [c["book"] for c in conv]
    return {
        # 第 2 版字段（保留）
        "books_used": list(averaged),
        "books_missing": {m["book"]: m["reason"] for m in missing_list},
        "majority_line": majority,
        "converted": legacy_converted,
        "min_books": MIN_BOOKS,
        # 第 3 版字段
        # 没有任何一家参与平均时，去水方法和换算模型都没有用到，输出空值（分析师 2026-10-14 决定）。
        "devig_method": DEVIG_METHOD if averaged else None,
        "conversion_model": model if averaged else None,
        "voting_books": list(voting),
        "averaged_books": averaged,
        "excluded_books": excl,
        "missing_books": missing_list,
        "tie_break": tie_break,
        "median_line": median if tie_break != "none" else None,
        "conversions": conv,
        "averaged_count": len(averaged),
    }


def goal_expectation(ou_lines: list[float]) -> tuple[float, str]:
    """亚盘换算用的总进球期望：同一场、同一阶段除威廉希尔外各家大小球盘口的中位数；没有时取 2.6。

    返回（取值, 来源说明代号）。调用方负责传入已去掉威廉希尔的盘口。
    """
    xs = [float(x) for x in ou_lines if x is not None and not math.isnan(float(x))]
    if xs:
        return float(statistics.median(xs)), "ou_median_same_phase"
    return DEFAULT_GOAL_EXPECTATION, "default"
