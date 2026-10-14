"""多家平均盘口换算（app/avg_conversion.py）的测试。

第一部分：拿算法顾问报告里的两份回测结果（report/backtest_events.csv 换盘事件、
report/backtest_crossbook.csv 跨机构同时点）逐行对答案，换算后水位与参考值相差必须在 0.001 以内。
参考结果由 analyze.py 产生，它的比分截断为每队 0 至 15 球、截断后不归一；接口默认用每队 0 至 10 球、
截断后归一（与测试用例参考实现 testcases/engine.py 一致）。两种设置都逐行对比。
研究目录不在本机时这一部分自动跳过。

第二部分：选多数盘口、平票、剔除、数据不足、欧赔等规则的单元测试，不依赖外部文件。
"""
from __future__ import annotations

import csv
import statistics
from pathlib import Path

import pytest

from app import avg_conversion as ac

RESEARCH = Path("/workspace/odds-data/research/avg-line-conversion-2026-10-14")
TOL = 0.001
# 两种比分截断设置：analyze.py 的设置，以及接口默认设置。
SETTINGS = {"analyze_15_raw": dict(max_goals=15, normalize=False),
            "api_default_10_normalized": dict(max_goals=ac.MAX_GOALS, normalize=True)}

needs_research = pytest.mark.skipif(not (RESEARCH / "report" / "backtest_events.csv").exists(),
                                    reason="算法顾问的研究目录不在本机")


def _rows(name: str) -> list[dict]:
    with open(RESEARCH / name, newline="") as f:
        return list(csv.DictReader(f))


def _snapshots() -> list[dict]:
    """按 analyze.py 的方式读入快照：亚盘盘口取反成接口写法，并整理出上、下两项水位和时点键。"""
    out = []
    for r in _rows("snapshots_all.csv"):
        ah = r["market"] == "ah"
        line = float(r["line"]) * (-1 if ah else 1)
        w1 = float(r["home_water"] if ah else r["over_water"])
        w2 = float(r["away_water"] if ah else r["under_water"])
        unknown = (r["opening_time_unknown"] or "0") in ("1", "1.0")
        out.append(dict(book=r["book"], market=r["market"], match=r["match_uid"], line=line, w1=w1, w2=w2,
                        tk="opening_time_unknown" if unknown else r["captured_at"]))
    return out


def _goal_expectations(snaps: list[dict]) -> dict[str, float]:
    """analyze.py 的总进球期望：同一场比赛全部大小球记录盘口的中位数。"""
    by: dict[str, list[float]] = {}
    for s in snaps:
        if s["market"] == "ou":
            by.setdefault(s["match"], []).append(s["line"])
    return {k: float(statistics.median(v)) for k, v in by.items()}


def compare_events(setting: dict) -> tuple[int, float]:
    """换盘事件：用换盘前的水位换算到换盘后的盘口，与参考的换算后水位对比。返回（行数, 最大绝对误差）。"""
    snaps = _snapshots()
    mu = _goal_expectations(snaps)
    events = [e for e in _rows("line_change_events.csv") if int(e["gap_sec"]) <= 1800]
    ref = _rows("report/backtest_events.csv")
    assert len(events) == len(ref)
    n, worst = 0, 0.0
    for e, r in zip(events, ref):
        sign = -1 if e["market"] == "ah" else 1
        lb, la = float(e["line_before"]) * sign, float(e["line_after"]) * sign
        assert abs(lb - float(r["Lb"])) < 1e-9 and abs(la - float(r["La"])) < 1e-9
        w1, w2 = float(e["water1_before"]), float(e["water2_before"])
        if r["qpred_power"] == "":
            continue
        _, k = ac.devig(w1, w2)
        q_ref = float(r["qpred_power"])
        expect = ac.revig([q_ref, 1 - q_ref], k)
        got = ac.convert(e["market"], w1, w2, lb, la, mu.get(e["match_uid"], 2.6), **setting)
        assert got is not None
        worst = max(worst, abs(got[0][0] - expect[0]), abs(got[0][1] - expect[1]))
        n += 1
    return n, worst


def compare_crossbook(setting: dict) -> tuple[int, float]:
    """跨机构同时点：按 analyze.py 的分组与遍历顺序重建每一对机构，比较换算后水位与对方实际水位的平均误差。

    参考文件里存的是 werr（换算后两项水位与对方实际水位之差的平均），所以这里比较的是
    我们算出的 werr 与参考 werr 之差。返回（行数, 最大绝对差）。
    """
    snaps = _snapshots()
    mu = _goal_expectations(snaps)
    groups: dict[tuple, list[dict]] = {}
    for s in snaps:
        groups.setdefault((s["match"], s["market"], s["tk"]), []).append(s)
    keep = {k: v for k, v in groups.items() if len({x["book"] for x in v}) >= 2}
    ref = _rows("report/backtest_crossbook.csv")
    i, worst = 0, 0.0
    for (match, market, tk) in sorted(keep, key=lambda k: (k[1], k[0], k[2])):
        rs = keep[(match, market, tk)]
        for a in rs:
            for b in rs:
                if a["book"] == b["book"] or a["line"] == b["line"]:
                    continue
                # 参考结果用 0 至 15 球不归一的设置判断有没有解；两种设置的可解范围在本数据上相同。
                c = ac.convert(market, a["w1"], a["w2"], a["line"], b["line"], mu.get(match, 2.6), **setting)
                if c is None:
                    continue
                r = ref[i]
                assert (r["market"], r["A"], r["B"]) == (market, a["book"], b["book"])
                werr = (abs(c[0][0] - b["w1"]) + abs(c[0][1] - b["w2"])) / 2
                worst = max(worst, abs(werr - float(r["werr"])))
                i += 1
    assert i == len(ref)
    return i, worst


@needs_research
@pytest.mark.parametrize("name", list(SETTINGS))
def test_reference_events(name):
    n, worst = compare_events(SETTINGS[name])
    assert n > 100
    assert worst < TOL, f"{name}：换盘事件最大误差 {worst}"


@needs_research
@pytest.mark.parametrize("name", list(SETTINGS))
def test_reference_crossbook(name):
    n, worst = compare_crossbook(SETTINGS[name])
    assert n > 1000
    assert worst < TOL, f"{name}：跨机构最大误差 {worst}"


# ----------------------------------------------------------------------------- 规则单元测试

def Q(line, w1, w2):
    return {"line": line, "water": [w1, w2]}


def test_quarter_line_split():
    assert ac.halves(0.25) == [0.0, 0.5]
    assert ac.halves(-0.75) == [-1.0, -0.5]
    assert ac.halves(2.5) == [2.5]
    assert ac.halves(1.0) == [1.0]


def test_same_line_water_unchanged_and_power_roundtrip():
    got, _ = ac.convert("ah", 0.92, 0.88, 0.5, 0.5, 2.6)
    assert got == [0.92, 0.88]
    p, k = ac.devig(0.92, 0.88)
    back = ac.revig(list(p), k)
    assert abs(back[0] - 0.92) < 1e-9 and abs(back[1] - 0.88) < 1e-9


def test_ah_direction_home_gives_more_lowers_home_water_side():
    # 主队多让一档，主队赢盘概率下降，主水应上升。
    got, _ = ac.convert("ah", 0.90, 0.90, 0.5, 0.75, 2.6)
    assert got[0] > 0.90 > got[1]
    got, _ = ac.convert("ou", 0.90, 0.90, 2.5, 2.75)
    assert got[0] > 0.90 > got[1]


def test_no_tie_all_ok():
    q = {"macau": Q(0.5, .92, .88), "crown": Q(0.5, .93, .89), "pinnacle": Q(0.5, .95, .91),
         "william": Q(0.75, 1.10, .70), "bet365": Q(0.5, .90, .90)}
    out = ac.compute_avg("ah", q, {}, mu=2.6)
    d = out["avg_detail"]
    assert out["status"] == "ok" and d["tie_break"] == "none" and d["median_line"] is None
    assert d["majority_line"] == 0.5 and d["averaged_count"] == 5 and d["voting_books"] == list(ac.BOOKS)
    assert [c["book"] for c in d["converted"]] == ["william"]
    assert out["values"]["line"] == 0.5


def test_ou_william_not_voting_but_averaged():
    q = {"macau": Q(2.5, .92, .88), "crown": Q(2.75, .93, .89), "william": Q(2.5, .80, 1.0), "bet365": Q(2.75, .9, .9)}
    d = ac.compute_avg("ou", q, {"pinnacle": "not_captured"})["avg_detail"]
    assert "william" not in d["voting_books"] and "william" in d["averaged_books"]
    # 投票机构澳门 2.5、皇冠 2.75、Bet365 2.75：多数 2.75，没有平票。
    assert d["majority_line"] == 2.75 and d["tie_break"] == "none"
    assert d["missing_books"] == [{"book": "pinnacle", "reason": "not_captured"}]
    assert d["books_missing"] == {"pinnacle": "not_captured"}


def test_tie_median_then_book_order():
    # 平票 0.25 与 0.5 各两家，中位数 0.375 距离相同，取澳门。
    q = {"macau": Q(0.5, .9, .9), "crown": Q(0.25, .9, .9), "pinnacle": Q(0.5, .9, .9), "bet365": Q(0.25, .9, .9)}
    d = ac.compute_avg("ah", q, {})["avg_detail"]
    assert d["tie_break"] == "macau" and d["majority_line"] == 0.5 and d["median_line"] == 0.375
    # 澳门缺失，取平博。
    q2 = {"crown": Q(0.25, .9, .9), "pinnacle": Q(0.5, .9, .9)}
    assert ac.pick_majority({k: v["line"] for k, v in q2.items()})[1] == "pinnacle"
    # 只剩皇冠和 Bet365。
    assert ac.pick_majority({"crown": 0.25, "bet365": 0.5}) == (0.25, "crown", 0.375)
    assert ac.pick_majority({"bet365": 0.5, "william": 0.25}) == (0.5, "bet365", 0.375)
    # 中位数能分开：0、0.5、0.5、1、1 → 0.5 与 1 各两票，中位数 0.5。
    assert ac.pick_majority({"a": 0, "b": 0.5, "c": 0.5, "d": 1, "e": 1})[:2] == (0.5, "median")


def test_tie_smaller_line():
    assert ac.pick_majority({"william": 0.25, "other": 0.5}) == (0.25, "smaller_line", 0.375)


def test_exclusion_still_votes_and_insufficient():
    q = {"macau": Q(0.5, .9, .9), "crown": Q(0.5, .9, .9), "william": Q(1.25, .9, .9)}
    out = ac.compute_avg("ah", q, {"pinnacle": "not_captured", "bet365": "no_source"})
    d = out["avg_detail"]
    assert d["voting_books"] == ["macau", "crown", "william"]
    assert d["excluded_books"] == [{"book": "william", "reason": "deviation_too_large", "steps": 3,
                                    "original_line": 1.25, "original_water": [0.9, 0.9], "margin": ac._r(ac.margin(.9, .9))}]
    # 2026-10-14 门槛改为至少两家：剔除一家后剩两家，照常输出平均值。
    assert out["status"] == "ok" and out["values"] is not None and d["averaged_count"] == 2


def test_one_book_or_none_is_insufficient():
    assert ac.MIN_BOOKS == 2
    out = ac.compute_avg("ah", {"macau": Q(0.5, .9, .9)}, {})
    assert out["status"] == "insufficient" and out["values"] is None
    out = ac.compute_avg("x1x2", {"macau": {"water": [2.0, 3.2, 3.6]}}, {})
    assert out["status"] == "insufficient" and out["values"] is None
    out = ac.compute_avg("ou", {}, {b: "not_captured" for b in ac.BOOKS})
    assert out["status"] == "insufficient" and out["values"] is None


def test_x1x2_simple_mean():
    q = {"macau": {"water": [2.0, 3.2, 3.6]}, "crown": {"water": [2.1, 3.3, 3.4]}, "william": {"water": [2.2, 3.4, 3.2]}}
    out = ac.compute_avg("x1x2", q, {"bet365": "not_captured"})
    d = out["avg_detail"]
    assert out["values"] == {"home": 2.1, "draw": 3.3, "away": 3.4}
    assert d["majority_line"] is None and d["conversion_model"] is None and d["voting_books"] == []
    assert all(c["steps"] == 0 and c["original_line"] is None and c["converted_water"] == c["original_water"]
               for c in d["conversions"])


# ----------------------------------------------------------------------------- 算法顾问的 27 个测试用例

CASES_FILE = RESEARCH / "testcases" / "cases.json"


def _load_cases() -> list[dict]:
    if not CASES_FILE.exists():
        return []
    import json
    return json.loads(CASES_FILE.read_text())["cases"]


def run_case(case: dict) -> list[str]:
    """用用例输入调用实现，逐项对比期望输出，返回不一致之处的说明（空列表表示全部一致）。"""
    quotes, missing = {}, {}
    for b, v in case["inputs"].items():
        if "missing" in v:
            missing[b] = v["missing"]
        elif "decimal_odds" in v:
            # 含本金的欧洲式赔率先减 1 换成香港盘水位（欧赔用例不会走到这里）。
            quotes[b] = {"line": v.get("line"), "water": [x - 1 for x in v["decimal_odds"]]}
        else:
            quotes[b] = {k: v[k] for k in ("line", "water") if k in v}
    out = ac.compute_avg(case["market"], quotes, missing, case.get("goal_expectation"))
    d, exp, tol, bad = out["avg_detail"], case["expected"], case.get("tolerance", 0.001), []
    for k in ("majority_line", "tie_break", "median_line", "voting_books", "averaged_books", "missing_books",
              "averaged_count", "devig_method", "conversion_model"):
        if k == "devig_method" and case["market"] == "x1x2" and d[k] is None and exp[k] == "power":
            # 接口约定已改为欧赔不去水、devig_method 为空；用例文件第 1 版仍写 "power"，以约定为准。
            continue
        if d[k] != exp[k]:
            bad.append(f"{k}: 实现 {d[k]} 期望 {exp[k]}")
    if exp["cell_status"] == "insufficient" and d["averaged_count"] >= ac.MIN_BOOKS:
        # 用例文件按旧门槛（至少三家）写；2026-10-14 门槛改为至少两家后，这类用例改为应输出平均值，数值不在用例里，只查状态。
        if out["status"] != "ok" or out["values"] is None:
            bad.append("门槛两家：应输出平均值")
        exp = {**exp, "cell_status": out["status"], "avg_water": None}
        out = {**out, "values": None}
    if out["status"] != exp["cell_status"]:
        bad.append(f"cell_status: 实现 {out['status']} 期望 {exp['cell_status']}")

    def close(a, b):
        return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= tol)

    for key in ("excluded_books", "conversions"):
        got, want = d[key], exp[key]
        if [x["book"] for x in got] != [x["book"] for x in want]:
            bad.append(f"{key} 机构不同")
            continue
        for g, w in zip(got, want):
            for f in ("original_line", "steps", "reason"):
                if g.get(f) != w.get(f):
                    bad.append(f"{key}.{g['book']}.{f}: 实现 {g.get(f)} 期望 {w.get(f)}")
            for f in ("original_water", "converted_water"):
                if f in w and not all(close(a, b) for a, b in zip(g[f], w[f])):
                    bad.append(f"{key}.{g['book']}.{f}: 实现 {g[f]} 期望 {w[f]}")
            if not close(g["margin"], w["margin"]):
                bad.append(f"{key}.{g['book']}.margin: 实现 {g['margin']} 期望 {w['margin']}")
    vals = out["values"]
    if exp.get("avg_water") is None:
        if vals is not None:
            bad.append("avg_water 应为空")
    else:
        keys = {"ah": ("home_water", "away_water"), "ou": ("over", "under"), "x1x2": ("home", "draw", "away")}
        got = [vals[k] for k in keys[case["market"]]] if vals else None
        if got is None or not all(close(a, b) for a, b in zip(got, exp["avg_water"])):
            bad.append(f"avg_water: 实现 {got} 期望 {exp['avg_water']}")
    return bad


@pytest.mark.skipif(not CASES_FILE.exists(), reason="算法顾问的测试用例文件不在本机")
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_advisor_cases(case):
    bad = run_case(case)
    assert not bad, "；".join(bad)


# ----------------------------------------------------------------------------- 两家门槛：AH-13 与 AH-15 逐项比对完整期望值
# 期望值抄自算法顾问 2026-10-14 重新生成的 cases.json（旧版备份 testcases/cases.v3.json），写死在测试里，不依赖用例文件。

def _ah(quotes, missing, ge=2.6):
    return ac.compute_avg("ah", quotes, missing, ge)


def _close_list(got, want, tol=0.001):
    return got is not None and len(got) == len(want) and all(abs(a - b) <= tol for a, b in zip(got, want))


def test_ah13_two_books_full_expectation():
    out = _ah({"william": {"line": 0.25, "water": [0.8, 0.98]}, "bet365": {"line": 0.5, "water": [1.0, 0.85]}},
              {"macau": "not_captured", "crown": "not_captured", "pinnacle": "not_captured"})
    d = out["avg_detail"]
    assert d["majority_line"] == 0.5
    assert d["tie_break"] == "bet365"
    assert d["median_line"] == 0.375
    assert d["voting_books"] == ["william", "bet365"]
    assert d["averaged_books"] == ["william", "bet365"]
    assert d["averaged_count"] == 2
    assert d["excluded_books"] == []
    assert d["missing_books"] == [{"book": "macau", "reason": "not_captured"}, {"book": "crown", "reason": "not_captured"},
                                  {"book": "pinnacle", "reason": "not_captured"}]
    conv = {c["book"]: c for c in d["conversions"]}
    assert list(conv) == ["william", "bet365"]
    assert conv["william"]["steps"] == 1 and _close_list(conv["william"]["converted_water"], [1.0429, 0.7515])
    assert abs(conv["william"]["margin"] - 0.0606) <= 0.001
    assert conv["bet365"]["steps"] == 0 and _close_list(conv["bet365"]["converted_water"], [1.0, 0.85])
    assert abs(conv["bet365"]["margin"] - 0.0405) <= 0.001
    assert out["status"] == "ok"
    assert _close_list([out["values"]["home_water"], out["values"]["away_water"]], [1.0214, 0.8007])


def test_ah15_excluded_plus_two_missing_full_expectation():
    out = _ah({"macau": {"line": 1.0, "water": [0.92, 0.92]}, "crown": {"line": 1.0, "water": [0.95, 0.93]},
               "pinnacle": {"line": 0.25, "water": [0.9, 1.0]}},
              {"william": "no_source", "bet365": "not_captured"})
    d = out["avg_detail"]
    assert d["majority_line"] == 1.0
    assert d["tie_break"] == "none"
    assert d["median_line"] is None
    assert d["voting_books"] == ["macau", "crown", "pinnacle"]
    assert d["averaged_books"] == ["macau", "crown"]
    assert d["averaged_count"] == 2
    ex = d["excluded_books"]
    assert [(e["book"], e["reason"], e["steps"], e["original_line"]) for e in ex] == [("pinnacle", "deviation_too_large", 3, 0.25)]
    assert abs(ex[0]["margin"] - 0.0263) <= 0.001
    assert d["missing_books"] == [{"book": "william", "reason": "no_source"}, {"book": "bet365", "reason": "not_captured"}]
    conv = {c["book"]: c for c in d["conversions"]}
    assert list(conv) == ["macau", "crown"]
    assert all(c["steps"] == 0 and c["converted_water"] == c["original_water"] for c in conv.values())
    assert abs(conv["macau"]["margin"] - 0.0417) <= 0.001 and abs(conv["crown"]["margin"] - 0.031) <= 0.001
    assert out["status"] == "ok"
    assert _close_list([out["values"]["home_water"], out["values"]["away_water"]], [0.935, 0.925])
