"""预警档位按每格抓取时刻固定基准（分析师、算法顾问 2026-10-14 决定）的测试。

前几例只用内存里的假数据；最后一例在设置了 ALERT_ASOF_V2D3_DB 时，以只读方式打开 v2d3 库做整表复核。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import alert_baseline as alb
from app import table_matches as tm

CN = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=CN)
KEY = ("ah", "crown", "mid")


def _base(points):
    b = alb.AsOfCellBaseline(tm.FALLBACK_QS)
    for pk, ts, v in points:
        b.add(KEY, pk, ts, v)
    b.freeze()
    return b


def test_sample_strictly_earlier_than_capture_time():
    b = _base([(1, T0, 0.90), (2, T0 + timedelta(minutes=1), 0.91), (3, T0 + timedelta(minutes=2), 0.92)])
    # 和抓取时刻相同的样本不算（严格早于）
    assert b.sample(KEY, 99, T0 + timedelta(minutes=1)) == [0.90]
    assert b.get(KEY, 99, T0)[1] == 0
    assert b.get(KEY, 99, T0 + timedelta(minutes=3))[1] == 3


def test_same_match_excluded():
    b = _base([(1, T0, 0.90), (2, T0 + timedelta(minutes=1), 0.91)])
    assert b.sample(KEY, 1, T0 + timedelta(hours=1)) == [0.91]
    assert b.get(KEY, 2, T0 + timedelta(hours=1))[1] == 1


def test_below_min_n_alert_is_null_and_sample_n_reported():
    pts = [(i, T0 + timedelta(minutes=i), 0.95 + (i % 5) * 0.001) for i in range(1, 60)]
    b = _base(pts)
    cell = {"recorded_at": (T0 + timedelta(days=1)).isoformat(), "return_rate": 0.80, "water_source": "actual"}
    alb.apply_alert_level(cell, KEY, 999, b, tm.fallback_hl_level, 100, tm._to_cn)
    assert cell["alert_sample_n"] == 59 and cell["alert_level"] is None
    alb.apply_alert_level(cell, KEY, 999, b, tm.fallback_hl_level, 50, tm._to_cn)
    assert cell["alert_level"] == "medium"
    # 没有抓取时刻：样本数 0，档位空值
    c2 = {"recorded_at": None, "return_rate": 0.80, "water_source": "actual"}
    alb.apply_alert_level(c2, KEY, 999, b, tm.fallback_hl_level, 1, tm._to_cn)
    assert c2["alert_sample_n"] == 0 and c2["alert_level"] is None


def test_recompute_later_with_more_data_gives_same_result():
    early = [(i, T0 + timedelta(minutes=i), 0.93 + (i % 7) * 0.003) for i in range(1, 130)]
    cell_at = T0 + timedelta(minutes=200)
    cell = {"recorded_at": cell_at.isoformat(), "return_rate": 0.935, "water_source": "actual"}
    b1 = _base(early)
    c1 = dict(cell)
    alb.apply_alert_level(c1, KEY, 500, b1, tm.fallback_hl_level, 100, tm._to_cn)
    # 之后又来了新数据（包括这一场自己的其他快照、抓取时刻之后的场次），再算一次结果不变
    later = early + [(500, cell_at - timedelta(minutes=5), 0.80)] + \
        [(1000 + i, cell_at + timedelta(minutes=i), 0.70) for i in range(0, 300)]
    b2 = _base(later)
    c2 = dict(cell)
    alb.apply_alert_level(c2, KEY, 500, b2, tm.fallback_hl_level, 100, tm._to_cn)
    assert (c1["alert_level"], c1["alert_sample_n"]) == (c2["alert_level"], c2["alert_sample_n"])
    assert c1["alert_sample_n"] == 129


def test_min_n_configurable(monkeypatch):
    monkeypatch.delenv("APP_ALERT_MIN_N", raising=False)
    assert alb.alert_min_n() == 100
    monkeypatch.setenv("APP_ALERT_MIN_N", "40")
    assert alb.alert_min_n() == 40
    monkeypatch.setenv("APP_ALERT_MIN_N", "abc")
    assert alb.alert_min_n() == 100


def test_apply_alert_only_ah_mid_close():
    blk = {p: {"alert_level": "light", "alert_sample_n": 120, "fallback_hl_level": "medium"} for p in tm.PHASES}
    tm._apply_alert(blk, "ah")
    assert blk["open"]["alert"] is None and blk["open"]["alert_sample_n"] is None
    assert blk["mid"]["alert"] == "light" and blk["mid"]["alert_sample_n"] == 120
    assert "alert_level" not in blk["close"]
    xb = {p: {"alert_level": "light", "alert_sample_n": 120} for p in tm.PHASES}
    tm._apply_alert(xb, "x1x2")
    assert all(xb[p]["alert"] is None and xb[p]["alert_sample_n"] is None for p in tm.PHASES)


_DB = os.environ.get("ALERT_ASOF_V2D3_DB")


def _cells(d):
    out = {}
    for it in d["items"]:
        for b, blk in it["ah"].items():
            for p in ("mid", "close"):
                c = (blk or {}).get(p) if isinstance(blk, dict) else None
                if isinstance(c, dict) and c.get("recorded_at"):
                    out[(it["match_id"], b, p)] = (c["recorded_at"], c["alert"], c["alert_sample_n"])
    return out


@pytest.mark.skipif(not _DB, reason="needs ALERT_ASOF_V2D3_DB (read-only v2d3 copy)")
def test_v2d3_alert_same_when_recomputed_at_different_times():
    conn = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    kw = dict(date_from="2000-01-01", date_to="2030-12-31", scope="all", strategy="CFFXDJ_5_V3", channel="rule",
              include_live="none", settlement_version=None, baseline_window_days=None, baseline_min_n=20,
              limit=2000, offset=0)
    now = datetime.now(CN)
    early = tm.build_table(conn, as_of=(now - timedelta(days=2)).isoformat(), now=now, **kw)
    late = tm.build_table(conn, as_of=None, now=now, **kw)
    ce, cl = _cells(early), _cells(late)
    cutoff = now - timedelta(days=2)
    common = [k for k in ce if k in cl and ce[k][0] == cl[k][0] and tm._to_cn(ce[k][0]) < cutoff]
    assert len(common) > 100
    assert all(ce[k][1:] == cl[k][1:] for k in common)
    for (_, _, _), (_, alert, n) in cl.items():
        if n is not None and n < alb.DEFAULT_ALERT_MIN_N:
            assert alert is None
