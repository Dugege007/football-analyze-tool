"""第二期剩余部分的测试：普通机构的大小球格子、Bet365 列、通用的格子状态 cell_status、升降 move。

全部用内存里构造的假数据，不连数据库。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import avg_cells as acl

CN = timezone(timedelta(hours=8))
KICK = datetime(2026, 10, 14, 20, 0, tzinfo=CN)
SCHED = {"mid_target_time": (KICK - timedelta(hours=8)).isoformat(),
         "close_target_time": (KICK - timedelta(hours=1)).isoformat()}


def _row(book, market, point, *, line=None, w=(None, None), x=(None, None, None), at=None, source="5df_live"):
    r = {"market": market, "book": book, "channel": "rule", "point": point, "source": source,
         "recorded_at": at.isoformat() if at else None, "extras_json": "{}", "line": line,
         "water_home": None, "water_away": None, "price_home": None, "price_away": None, "price_draw": None,
         "water_over": None, "water_under": None, "price_over": None, "price_under": None}
    if market == "asian":
        r["water_home"], r["water_away"] = w
    elif market == "ou":
        r["water_over"], r["water_under"] = w
    else:
        r["price_home"], r["price_draw"], r["price_away"] = x
    return r


def _data(rows):
    return SimpleNamespace(snap_by_match={1: rows}, timeline={})


def _full_rows():
    rows = []
    t = {"open": KICK - timedelta(hours=20), "mid": KICK - timedelta(hours=8), "close": KICK - timedelta(hours=1)}
    for b in ("macau", "crown", "pinnacle", "william", "bet365"):
        for p, at in t.items():
            ou_line = 2.5 if b != "william" else 3.0
            ou_w = (0.90, 0.92) if p != "close" else (0.95, 0.87)
            rows.append(_row(b, "ou", p, line=ou_line, w=ou_w, at=at))
            # 数据库亚盘写法：主队让球为负。
            ah_line = -0.5 if p == "open" else -0.75
            rows.append(_row(b, "asian", p, line=ah_line, w=(0.90, 0.92), at=at))
            rows.append(_row(b, "euro_1x2", p, x=(2.0, 3.4, 3.6) if p == "open" else (1.9, 3.5, 3.9), at=at))
    return rows


PLAIN = {"ou": ("macau", "crown", "pinnacle", "william", "bet365"), "ah": ("bet365",), "x1x2": ("bet365",)}


def test_ou_cells_for_all_five_books_and_bet365_columns():
    out = acl.build_tables(_data(_full_rows()), 1, KICK, SCHED, KICK + timedelta(hours=3), PLAIN)
    ou = out["plain"]["ou"]
    assert set(ou) == set(PLAIN["ou"])
    c = ou["william"]["open"]
    assert (c["line"], c["over"], c["under"], c["cell_status"]) == (3.0, 0.9, 0.92, "ok")
    assert c["source"] == "5df_live" and c["recorded_at"] is not None
    b365 = out["plain"]["ah"]["bet365"]["mid"]
    assert b365["line"] == 0.75 and b365["home_water"] == 0.9 and b365["cell_status"] == "ok"
    x = out["plain"]["x1x2"]["bet365"]["close"]
    assert (x["home"], x["draw"], x["away"]) == (1.9, 3.5, 3.9)
    # 多家平均和普通格子同一次取数：大小球五家都有数据，平均格为 ok。
    assert out["avg"]["ou"]["close"]["cell_status"] == "ok"
    assert out["avg"]["ou"]["close"]["avg_detail"]["averaged_count"] == 5


def test_move_rules():
    out = acl.build_tables(_data(_full_rows()), 1, KICK, SCHED, KICK + timedelta(hours=3), PLAIN)
    ou = out["plain"]["ou"]["macau"]
    assert ou["open"]["move"] is None
    assert ou["mid"]["move"] == {"compare_to": "open", "line": "flat", "left": "flat", "right": "flat"}
    assert ou["close"]["move"] == {"compare_to": "mid", "line": "flat", "left": "up", "right": "down"}
    ah = out["plain"]["ah"]["bet365"]
    # 盘口有变化（主让半球升到主让半一）：只判盘口，水位记为换盘不比较。
    assert ah["mid"]["move"] == {"compare_to": "open", "line": "up", "left": "not_comparable",
                                 "right": "not_comparable"}
    # 盘口没变：水位按数值比较，不设阈值（0.90 和 0.90 为 flat）。
    assert ah["close"]["move"] == {"compare_to": "mid", "line": "flat", "left": "flat", "right": "flat"}
    x = out["plain"]["x1x2"]["bet365"]["mid"]["move"]
    assert x == {"compare_to": "open", "line": None, "left": "down", "mid": "up", "right": "up"}


def test_close_compares_to_open_when_mid_missing():
    rows = [r for r in _full_rows() if not (r["point"] == "mid" and r["book"] == "crown")]
    out = acl.build_tables(_data(rows), 1, KICK, SCHED, KICK + timedelta(hours=3), PLAIN)
    c = out["plain"]["ou"]["crown"]
    assert c["mid"]["cell_status"] == "unavailable" and c["mid"]["cell_status_reason"] == "not_captured"
    assert c["close"]["move"]["compare_to"] == "open"


def test_ah_line_move_side_change():
    assert acl.ah_line_move(0.5, 0.25) == "up"
    assert acl.ah_line_move(0.25, 0.5) == "down"
    assert acl.ah_line_move(-0.5, -0.25) == "up"     # 客队让得更多
    assert acl.ah_line_move(0.25, -0.25) == "up"     # 换边：主队从受让变成让球
    assert acl.ah_line_move(0.0, 0.25) == "down"
    assert acl.ah_line_move(0.0, 0.0) == "flat"
    assert acl.ah_line_move(None, 0.5) is None


def test_pending_reasons_before_kickoff():
    as_of = KICK - timedelta(hours=10)  # 中盘目标时刻还没到
    rows = [r for r in _full_rows() if r["point"] == "open" and r["book"] != "bet365"]
    out = acl.build_tables(_data(rows), 1, KICK, SCHED, as_of, PLAIN)
    ou = out["plain"]["ou"]
    assert ou["bet365"]["open"]["cell_status"] == "pending"
    assert ou["bet365"]["open"]["cell_status_reason"] == "book_not_open"
    assert ou["macau"]["mid"]["cell_status_reason"] == "not_due"
    assert ou["macau"]["close"]["cell_status"] == "pending"


def test_apply_cell_status_existing_cells():
    late = KICK + timedelta(hours=3)
    c = {"line": 0.5, "home_water": 0.9, "away_water": 0.92}
    acl.apply_cell_status("ah", "mid", c, KICK, SCHED, late)
    assert (c["cell_status"], c["cell_status_reason"]) == ("ok", None)
    t = {"line": 0.5, "home_water": 0.9, "away_water": 0.92, "status": "suspect_truncated"}
    acl.apply_cell_status("ah", "open", t, KICK, SCHED, late)
    assert (t["cell_status"], t["cell_status_reason"]) == ("unavailable", "suspect_truncated")
    half = {"home": 2.0, "draw": None, "away": None}
    acl.apply_cell_status("x1x2", "close", half, KICK, SCHED, late)
    assert (half["cell_status"], half["cell_status_reason"]) == ("unavailable", "not_captured")
    empty = {"home": None, "draw": None, "away": None}
    acl.apply_cell_status("jc_1x2", "close", empty, KICK, SCHED, KICK - timedelta(hours=2))
    assert (empty["cell_status"], empty["cell_status_reason"]) == ("pending", "not_due")
    keep = {"cell_status": "insufficient", "cell_status_reason": "too_few_books"}
    acl.apply_cell_status("ah", "close", keep, KICK, SCHED, late)
    assert keep["cell_status"] == "insufficient"
    acl.apply_cell_status("ah", "close", None, KICK, SCHED, late)  # 空格子不报错


def test_tiny_water_change_counts():
    rows = _full_rows()
    for r in rows:
        if r["book"] == "pinnacle" and r["market"] == "ou" and r["point"] == "mid":
            r["water_over"] = 0.9001
    out = acl.build_tables(_data(rows), 1, KICK, SCHED, KICK + timedelta(hours=3), PLAIN)
    assert out["plain"]["ou"]["pinnacle"]["mid"]["move"]["left"] == "up"


def test_ou_line_change_not_comparable():
    rows = _full_rows()
    for r in rows:
        if r["book"] == "crown" and r["market"] == "ou" and r["point"] == "close":
            r["line"] = 2.25
    out = acl.build_tables(_data(rows), 1, KICK, SCHED, KICK + timedelta(hours=3), PLAIN)
    assert out["plain"]["ou"]["crown"]["close"]["move"] == {"compare_to": "mid", "line": "down",
                                                           "left": "not_comparable", "right": "not_comparable"}


# ---- 研究副本上的整表检查（本机没有副本时跳过） ----
import sqlite3
from pathlib import Path

import pytest

from app import table_matches as tm

V2D3_DB = Path("/workspace/match-analysis-api/data/v2d3/app.db")


@pytest.fixture(scope="module")
def v2d3_table():
    if not V2D3_DB.exists():
        pytest.skip("本机没有研究副本 v2d3")
    conn = sqlite3.connect(f"file:{V2D3_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return tm.build_table(conn, date_from="2000-01-01", date_to="2099-12-31", scope="all", strategy="none",
                          channel="rule", include_live="none", settlement_version=None, as_of=None,
                          baseline_window_days=None, baseline_min_n=20, limit=100000, offset=0)


def test_v2d3_macau_rename(v2d3_table):
    for it in v2d3_table["items"]:
        ah = it["ah"]
        assert "macau_5df" not in ah  # 别名已删除（2026-10-14）
        assert "macau_manual" in ah
        for ph in ("open", "mid", "close"):
            c = ah["macau"][ph]
            assert c.get("book_lane") in (None, "macau_5df")
            if c.get("source"):
                assert c["source"] in MACAU_CODES
            m = ah["macau_manual"][ph]
            assert m.get("book_lane") in (None, "macau_manual")


MACAU_CODES = {"5df", "5df_live", "5df_live_opening", "5df_hist_asof_full", "5df_hist_asof", "5df_macauslot_history",
               "5dollar_history", "5df_odds_snap"}
# 约定 7.8 节的来源代码（2026-10-14 起所有格子的 source 都写这些代码）。
SOURCE_CODES = MACAU_CODES | {"legacy_import", "legacy_home_only", "avg"}


def test_v2d3_sources_are_codes(v2d3_table):
    """所有格子的 source 是来源代码；原来的取数路径放进 source_path（分析师 2026-10-14 决定）。"""
    import collections
    seen = collections.Counter()
    for it in v2d3_table["items"]:
        for mk in ("ah", "ou", "x1x2"):
            for b, blk in it[mk].items():
                if not isinstance(blk, dict):
                    continue
                for ph in ("open", "mid", "close"):
                    c = blk.get(ph)
                    if not isinstance(c, dict):
                        continue
                    assert "_source_code" not in c and "_source_path" not in c
                    if c.get("source") is None:
                        continue
                    assert "/" not in c["source"], (mk, b, ph, c["source"])
                    assert c["source"] in SOURCE_CODES, (mk, b, ph, c["source"])
                    seen[c["source"]] += 1
                    if c.get("source_path") is not None:
                        assert c["source_path"] != c["source"]
    assert seen
    import json
    assert "_source_code" not in json.dumps(v2d3_table) and "_source_path" not in json.dumps(v2d3_table)
    for it in v2d3_table["items"]:
        for mk in ("ah", "x1x2"):
            for b, blk in it[mk].items():
                for ph in ("last_prematch", "api_closing"):
                    c = blk.get(ph) if isinstance(blk, dict) else None
                    if isinstance(c, dict) and c.get("source"):
                        assert "/" not in c["source"], (mk, b, ph, c["source"])


def test_source_code_of_paths():
    from app import table_matches as tm
    assert tm.source_code_of("odds_timeline_seg/5df_hist_asof_full") == "5df_hist_asof_full"
    assert tm.source_code_of("odds_snapshot/rule/close") == "odds_snapshot/rule/close"
    assert tm.source_code_of("odds_snapshot/rule/close", "5df_live") == "5df_live"
    assert tm.source_code_of("odds_snapshot/actual/t1/5df_live") == "5df_live"
    assert tm.source_code_of("5df_odds_history/chinasportslottery/1x2 (5dollar_history)") == "5dollar_history"
    assert tm.source_code_of("odds_asian") == "legacy_import"
    assert tm.source_code_of("odds_jc_home(home_only)") == "legacy_home_only"
    assert tm.source_code_of("avg") == "avg"
    c = {"source": "odds_snapshot/rule/mid", "_source_code": "5df_live"}
    tm._normalize_one(c)
    assert c == {"source": "5df_live", "source_path": "odds_snapshot/rule/mid"}
    c = {"source": "5df", "_source_path": "odds_snapshot/rule/mid", "_source_code": "5df_macauslot_history"}
    tm._normalize_one(c)
    assert c == {"source": "5df_macauslot_history", "source_path": "odds_snapshot/rule/mid"}
    c = {"source": "5df_live"}
    tm._normalize_one(c)
    assert c == {"source": "5df_live", "source_path": None}


def test_v2d3_moves_and_alert(v2d3_table):
    seen_nc = 0
    for it in v2d3_table["items"]:
        for mk in ("ah", "ou", "x1x2"):
            for b, blk in it[mk].items():
                if b == "avg" or not isinstance(blk, dict):
                    continue
                assert blk["open"].get("move") is None
                assert blk["open"].get("alert") is None  # 初盘不出预警（分析师 2026-10-14 决定）
                for ph in ("mid", "close"):
                    c = blk[ph]
                    mv = c.get("move")
                    if c.get("cell_status") != "ok":
                        assert mv is None
                    if mv and mk != "x1x2" and mv["line"] != "flat":
                        assert mv["left"] == mv["right"] == "not_comparable"
                        seen_nc += 1
                    if mk == "x1x2":
                        assert c.get("alert") is None  # 欧赔不显示预警（分析师 2026-10-14 决定）
                    else:
                        # 2026-10-14：预警档位改按这一格抓取时刻固定的基准，样本不足 100 时为空值。
                        n = c.get("alert_sample_n")
                        if mk == "ou" or n is None or n < 100:
                            assert c.get("alert") is None
                        else:
                            assert c.get("alert") in (None, "light", "medium", "high")
    assert seen_nc > 0


def test_recorded_at_is_cn_for_utc_sources():
    rows = _full_rows()
    for r in rows:
        if r["book"] == "crown" and r["point"] == "mid":
            r["recorded_at"] = (KICK - timedelta(hours=8)).astimezone(timezone.utc).isoformat()
            r["source"] = "5df_hist_asof_full"
    out = acl.build_tables(_data(rows), 1, KICK, SCHED, KICK + timedelta(hours=3), PLAIN)
    src = out["avg"]["ah"]["mid"]["avg_detail"]["sources"]["crown"]
    assert src["recorded_at"].endswith("+08:00") and src["recorded_at"].startswith("2026-10-14T12:00")
    assert out["plain"]["ou"]["crown"]["mid"]["recorded_at"].endswith("+08:00")


def test_no_averaged_books_nulls_method_fields():
    out = acl.build_tables(_data([]), 1, KICK, SCHED, KICK + timedelta(hours=3), PLAIN)
    for mk in ("ah", "ou", "x1x2"):
        det = out["avg"][mk]["open"]["avg_detail"]
        assert det["devig_method"] is None and det["averaged_count"] == 0
        assert det["conversion_model"] is None
    det = out["avg"]["ah"]["open"]["avg_detail"]
    assert det["goal_expectation"] is None and det["goal_expectation_source"] is None
    # 有机构参与平均但不足 3 家：照常输出。
    rows = [r for r in _full_rows() if r["book"] in ("macau", "crown")]
    det = acl.build_tables(_data(rows), 1, KICK, SCHED, KICK + timedelta(hours=3), PLAIN)["avg"]["ah"]["open"]["avg_detail"]
    assert det["averaged_count"] == 2 and det["devig_method"] == "power" and det["goal_expectation"] == 2.5


def test_v2d3_alias_removed_and_alert_levels(v2d3_table):
    import collections
    lv = collections.Counter()
    for it in v2d3_table["items"]:
        assert "macau_5df" not in it["ah"]
        for b, blk in it["ah"].items():
            if isinstance(blk, dict) and b != "avg":
                for ph in ("open", "mid", "close"):
                    lv[blk[ph].get("alert")] += 1
    assert lv["high"] + lv["medium"] + lv["light"] > 0
