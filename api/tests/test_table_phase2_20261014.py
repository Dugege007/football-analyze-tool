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
    assert ah["mid"]["move"]["line"] == "up"  # 主让半球升到主让半一
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
