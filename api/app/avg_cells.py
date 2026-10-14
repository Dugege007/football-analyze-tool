"""数据表接口里「多家平均」三格（亚盘 ah.avg、大小球 ou.avg、欧赔 x1x2.avg）的取数与组装。

计算规则见接口约定第 3 版第 7 节（/workspace/odds-data/ui-feedback/20261014/api-contract-phase1.md），
纯数学部分在 app/avg_conversion.py。本模块只读已加载到内存的快照，不写数据库。

取数口径（本模块的实现选择，已写进验收说明请分析师确认）：
- 只用 odds_snapshot 的规则通道（channel=rule）在 open、mid、close 三个点位的记录；
  不用 legacy_import 和带 manual 字样的来源（用户已定不再使用手工数据）。
- 初盘优先取 5df_live_opening（接口给出的开盘盘口，没有开盘时间）；没有时取这家机构最早的一条带时间的规则记录；
  再没有时取这家机构时间线上最早的一段，但只在它距开赛超过 8 小时时才算初盘。
  距开赛超过 8 小时、但不是第一条的记录（early 记录）一律不用。
- 中盘、临盘取规则通道的 mid、close 点位；同一点位有多个来源时，依次优先 5df_live、5df_hist_asof、其他来源。
- 截止时刻（as_of）之后才记录的数据不用。
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta
from typing import Any, Optional

from app import avg_conversion as ac

PHASES = ("open", "mid", "close")
MARKETS = {"ah": "asian", "ou": "ou", "x1x2": "euro_1x2"}
_EXCLUDED_SOURCES = {"legacy_import"}
_MIDCLOSE_PRIORITY = {"5df_live": 0, "5df_hist_asof": 1}
OPEN_MIN_LEAD = timedelta(hours=8)


def _parse(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        from app import collection_schedule as cs
        dt = dt.replace(tzinfo=cs.TZ_CN)
    return dt


def _usable_source(src: Any) -> bool:
    s = src or ""
    return s not in _EXCLUDED_SOURCES and "manual" not in s


def _fetched_at(r: Any) -> Optional[datetime]:
    try:
        ex = json.loads(r["extras_json"] or "{}")
    except (TypeError, ValueError):
        return None
    return _parse(ex.get("fetched_at")) if isinstance(ex, dict) else None


def _hk(water: Any, price: Any) -> Optional[float]:
    """香港盘水位：有水位直接用；只有含本金的赔率时减 1。"""
    if water is not None:
        return float(water)
    if price is not None:
        return float(price) - 1.0
    return None


def _quote(market: str, r: Any) -> Optional[dict]:
    """把一条记录整理成计算用的报价；字段不全时返回 None。亚盘盘口换成接口写法（主队让球为正）。"""
    if market == "x1x2":
        o = [r["price_home"], r["price_draw"], r["price_away"]]
        if any(x is None for x in o):
            return None
        return {"water": [float(x) for x in o]}
    if r["line"] is None:
        return None
    if market == "ah":
        w = [_hk(r["water_home"], r["price_home"]), _hk(r["water_away"], r["price_away"])]
        line = -float(r["line"]) + 0.0
    else:
        w = [_hk(r["water_over"], r["price_over"]), _hk(r["water_under"], r["price_under"])]
        line = float(r["line"])
    if any(x is None for x in w):
        return None
    return {"line": line, "water": w}


def _record_time(r: Any, key: str) -> Optional[datetime]:
    return _parse(r[key])


def select_quotes(d: Any, mpk: int, market: str, phase: str, kick: Optional[datetime],
                  as_of: datetime) -> dict[str, dict]:
    """返回 {机构: 报价}，报价另带 source 与 recorded_at 便于排查。"""
    db_market = MARKETS[market]
    cands: dict[str, list[tuple]] = {}
    for r in d.snap_by_match.get(mpk, []):
        if (r["market"] != db_market or r["book"] not in ac.BOOKS or r["channel"] != "rule"
                or r["point"] != phase or not _usable_source(r["source"])):
            continue
        t = _record_time(r, "recorded_at")
        if t is None and r["source"] != "5df_live_opening":
            continue
        seen = t or _fetched_at(r)
        if seen is not None and seen > as_of:
            continue
        q = _quote(market, r)
        if q is None:
            continue
        if phase == "open":
            rank = (0 if r["source"] == "5df_live_opening" else 1, t.timestamp() if t else 0.0)
        else:
            rank = (_MIDCLOSE_PRIORITY.get(r["source"], 9), -(t.timestamp() if t else 0.0))
        q.update(source=r["source"], recorded_at=t.isoformat() if t else None)
        cands.setdefault(r["book"], []).append((rank, q))
    out = {b: min(v, key=lambda x: x[0])[1] for b, v in cands.items()}
    if phase == "open" and kick is not None:
        # 规则通道没有初盘时，退回这家机构时间线上最早的一段（距开赛超过 8 小时才算初盘）。
        first: dict[str, tuple] = {}
        for r in d.timeline.get(mpk, []):
            if r["market"] != db_market or r["book"] not in ac.BOOKS or r["is_inplay"] or not _usable_source(r["source"]):
                continue
            t = _record_time(r, "seg_start_at")
            if t is None or t > as_of:
                continue
            if r["book"] not in first or t < first[r["book"]][0]:
                first[r["book"]] = (t, r)
        for b, (t, r) in first.items():
            if b in out or kick - t <= OPEN_MIN_LEAD:
                continue
            q = _quote(market, r)
            if q is not None:
                q.update(source=r["source"], recorded_at=t.isoformat())
                out[b] = q
    return out


def missing_reason(phase: str, kick: Optional[datetime], sched: dict, as_of: datetime) -> str:
    """没有数据的机构的原因（第 2 节判定顺序的简化版，见模块说明）。"""
    if kick is None:
        return "not_captured"
    if phase == "open":
        return "book_not_open" if as_of < kick else "not_captured"
    target = _parse(sched.get(f"{phase}_target_time"))
    if target is None:
        target = kick - (timedelta(hours=8) if phase == "mid" else timedelta(hours=1))
    return "not_due" if as_of < target and as_of < kick else "not_captured"


def build_avg_cell(market: str, phase: str, quotes: dict[str, dict], missing: dict[str, str],
                   mu: Optional[float], mu_source: Optional[str]) -> dict:
    res = ac.compute_avg(market, quotes, missing, mu)
    detail = res["avg_detail"]
    if market == "ah":
        detail["goal_expectation"] = mu
        detail["goal_expectation_source"] = mu_source
    keys = {"ah": ("line", "home_water", "away_water"), "ou": ("line", "over", "under"),
            "x1x2": ("home", "draw", "away")}[market]
    cell: dict[str, Any] = {k: None for k in keys}
    if res["values"]:
        cell.update(res["values"])
    if res["status"] == "ok":
        status, reason = "ok", None
    elif not quotes:
        reasons = [missing[b] for b in ac.BOOKS if b in missing]
        if any(r in ("not_due", "book_not_open") for r in reasons):
            status = "pending"
            reason = "not_due" if "not_due" in reasons else "book_not_open"
        else:
            status = "unavailable"
            reason = reasons[0] if reasons and len(set(reasons)) == 1 else "not_captured"
    else:
        status, reason = "insufficient", "too_few_books"
    cell.update({"cell_status": status, "cell_status_reason": reason, "source": "avg", "recorded_at": None,
                 "move": None, "alert": None, "avg_detail": detail})
    return cell


def build_avg_block(d: Any, mpk: int, kick: Optional[datetime], sched: dict, as_of: datetime) -> dict:
    """返回 {"ah": {phase: 格子}, "ou": {...}, "x1x2": {...}}。"""
    return build_tables(d, mpk, kick, sched, as_of, {})["avg"]


# ---------------------------------------------------------------------------
# 第二期剩余部分：普通机构的大小球格子、Bet365 列、通用的格子状态 cell_status（接口约定第 2、3、4 节）。
# 普通机构的大小球格子和 Bet365 的亚盘、欧赔格子，与多家平均用同一套取数（select_quotes），
# 这样页面上每家的格子和多家平均的输入完全一致。
# ---------------------------------------------------------------------------

PLAIN_KEYS = {"ah": ("line", "home_water", "away_water"), "ou": ("line", "over", "under"),
              "x1x2": ("home", "draw", "away")}
# 判断「有数据」时必须齐全的字段。
DATA_KEYS = {"ah": ("line", "home_water", "away_water"), "ou": ("line", "over", "under"),
             "x1x2": ("home", "draw", "away"), "jc_1x2": ("home", "draw", "away"),
             "jc_hhad": ("goal_line", "home", "draw", "away")}


def plain_cell(market: str, q: Optional[dict], reason: str) -> dict:
    """由一条报价组装普通机构的格子；没有报价时各值为空，并写明缺席原因。"""
    keys = PLAIN_KEYS[market]
    cell: dict[str, Any] = {k: None for k in keys}
    if q is not None:
        vals = ([q["line"]] if market != "x1x2" else []) + list(q["water"])
        cell.update({k: (round(float(v), 4) if v is not None else None) for k, v in zip(keys, vals)})
        cell.update(source=q.get("source"), recorded_at=q.get("recorded_at"),
                    cell_status="ok", cell_status_reason=None)
    else:
        cell.update(source=None, recorded_at=None,
                    cell_status="pending" if reason in ("not_due", "book_not_open") else "unavailable",
                    cell_status_reason=reason)
    cell.update(move=None, alert=None)
    return cell


def _cmp(new: Any, old: Any) -> Optional[str]:
    if new is None or old is None:
        return None
    new, old = float(new), float(old)
    return "up" if new > old + 1e-9 else "down" if new < old - 1e-9 else "flat"


def ah_line_move(new: Any, old: Any) -> Optional[str]:
    """亚盘盘口的升降（接口写法，主队让球为正）：让球方让得更多为升，让得更少为降。
    让球方换边时按新的让球方判断：新的让球方让的球数比它原来让的（原来受让记为负数）多，就是升。"""
    if new is None or old is None:
        return None
    new, old = float(new), float(old)
    if abs(new) < 1e-9:
        return "flat" if abs(old) < 1e-9 else "down"
    sign = 1.0 if new > 0 else -1.0
    return _cmp(new * sign, old * sign)


NOT_COMPARABLE = "not_comparable"


def apply_moves(market: str, cells: dict) -> None:
    """按约定第 4 节（分析师 2026-10-14 确认）给中盘、临盘写 move。

    - 中盘和初盘比，临盘和中盘比；中盘没有数据时，临盘和初盘比。
    - 亚盘、大小球：盘口有变化时只判断盘口升降，两边水位记为 not_comparable（换盘，不比较）；
      盘口没有变化时，盘口记为 flat，水位按数值比较，任何变化都算，不设阈值。
    - 欧赔没有盘口，三项赔率直接比较。
    已有的格子和新格子都用这一个函数；格子状态不是 ok 的不参与比较，move 为空值。
    """
    keys = PLAIN_KEYS[market]

    def has(c):
        return isinstance(c, dict) and c.get("cell_status") == "ok"

    for ph in ("open", "mid", "close"):
        if isinstance(cells.get(ph), dict):
            cells[ph]["move"] = None
    for ph in ("mid", "close"):
        cur = cells.get(ph)
        if not has(cur):
            continue
        ref_name = "open" if ph == "mid" else ("mid" if has(cells.get("mid")) else "open")
        ref = cells.get(ref_name)
        if not has(ref):
            continue
        if market == "x1x2":
            cur["move"] = {"compare_to": ref_name, "line": None, "left": _cmp(cur["home"], ref["home"]),
                           "mid": _cmp(cur["draw"], ref["draw"]), "right": _cmp(cur["away"], ref["away"])}
            continue
        line_mv = ah_line_move(cur["line"], ref["line"]) if market == "ah" else _cmp(cur["line"], ref["line"])
        if line_mv != "flat":
            left = right = NOT_COMPARABLE
        else:
            left, right = _cmp(cur[keys[1]], ref[keys[1]]), _cmp(cur[keys[2]], ref[keys[2]])
        cur["move"] = {"compare_to": ref_name, "line": line_mv, "left": left, "right": right}


def build_tables(d: Any, mpk: int, kick: Optional[datetime], sched: dict, as_of: datetime,
                 plain: dict[str, tuple[str, ...]]) -> dict:
    """一次取数同时组装多家平均和普通机构的格子。

    plain：{盘种: 需要组装普通格子的机构}，例如 {"ou": 五家, "ah": ("bet365",), "x1x2": ("bet365",)}。
    返回 {"avg": {盘种: {阶段: 格子}}, "plain": {盘种: {机构: {阶段: 格子}}}}。
    """
    avg: dict[str, dict] = {m: {} for m in MARKETS}
    pl: dict[str, dict] = {m: {b: {} for b in plain.get(m, ())} for m in MARKETS}
    for phase in PHASES:
        sel = {m: select_quotes(d, mpk, m, phase, kick, as_of) for m in MARKETS}
        reason = missing_reason(phase, kick, sched, as_of)
        ou_lines = [q["line"] for b, q in sel["ou"].items() if b != "william"]
        mu, mu_src = ac.goal_expectation(ou_lines)
        for m in MARKETS:
            quotes = {b: {k: v for k, v in q.items() if k in ("line", "water")} for b, q in sel[m].items()}
            miss = {b: reason for b in ac.BOOKS if b not in quotes}
            cell = build_avg_cell(m, phase, quotes, miss, mu if m == "ah" else None, mu_src if m == "ah" else None)
            cell["avg_detail"]["sources"] = {b: {"source": q.get("source"), "recorded_at": q.get("recorded_at")}
                                             for b, q in sel[m].items()}
            avg[m][phase] = cell
            for b in plain.get(m, ()):
                pl[m][b][phase] = plain_cell(m, sel[m].get(b), reason)
    for m, blk in pl.items():
        for cells in blk.values():
            apply_moves(m, cells)
    return {"avg": avg, "plain": pl}


def _has_data(market: str, cell: dict) -> bool:
    return all(cell.get(k) is not None for k in DATA_KEYS[market])


def apply_cell_status(market: str, phase: str, cell: Any, kick: Optional[datetime], sched: dict,
                      as_of: datetime) -> None:
    """给已有的普通格子（亚盘、欧赔、竞彩）补上通用的 cell_status 和 cell_status_reason（第 2 节）。

    已经带 cell_status 的格子（多家平均、新组装的格子）不改。
    - 字段齐全：ok；但初盘被截断判定为不可信时为 unavailable，原因 suspect_truncated。
    - 只有部分字段（例如欧赔只有主胜）：unavailable，原因 not_captured，不当作有数据。
    - 完全没有：按目标时刻判 pending（not_due、book_not_open）或 unavailable（not_captured）。
    """
    if not isinstance(cell, dict) or "cell_status" in cell:
        return
    if _has_data(market, cell):
        if phase == "open" and cell.get("status") == "suspect_truncated":
            cell["cell_status"], cell["cell_status_reason"] = "unavailable", "suspect_truncated"
        else:
            cell["cell_status"], cell["cell_status_reason"] = "ok", None
        return
    if any(cell.get(k) is not None for k in DATA_KEYS[market] if k not in ("line", "goal_line")):
        cell["cell_status"], cell["cell_status_reason"] = "unavailable", "not_captured"
        return
    reason = missing_reason(phase, kick, sched, as_of)
    cell["cell_status"] = "pending" if reason in ("not_due", "book_not_open") else "unavailable"
    cell["cell_status_reason"] = reason
