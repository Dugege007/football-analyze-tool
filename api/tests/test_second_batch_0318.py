"""0.3.18 第二批口径 A–F：例外场按编号 / kickoff_jc / N1 close_unusable / 欧赔 water_source / §2 真实水位 + tier_cross。"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import app.db as adb  # noqa: E402
from app import collection_schedule as cs  # noqa: E402
from app import shadow_evaluable as se  # noqa: E402
from app import table_matches as tm  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PROD_DB = ROOT / "data" / "app.db"
V2D3_DB = ROOT / "data" / "v2d3" / "app.db"


def _dt(s):
    return datetime.fromisoformat(s)


# ---------------- 1. 例外场按编号（exception_rule=jc_code_ge_2300） ----------------

@pytest.mark.parametrize("kick,coded,want", [
    ("2026-06-14T12:00:00+08:00", True, True),    # 世界杯 3 场：属 D、次日 12:00 → 例外（无上限）
    ("2026-06-14T12:00:00+08:00", False, False),  # 无编号仍按 [D 23:00, D+1 11:30]
    ("2026-06-14T11:30:00+08:00", False, True),
    ("2026-06-13T23:00:00+08:00", True, True),
    ("2026-06-13T22:59:00+08:00", True, False),
    ("2026-06-13T19:00:00+08:00", True, False),
])
def test_phase_exception_by_code(kick, coded, want):
    k = _dt(kick)
    assert cs.phase_exception("2026-06-13", k, k.hour, True, coded) is want


def test_rule_targets_coded_exception_and_real_targets():
    k = _dt("2026-06-14T12:00:00+08:00")
    mid, close = cs.rule_targets("2026-06-13", 12, k, True, has_jc_code=True)
    assert (mid.isoformat(), close.isoformat()) == ("2026-06-13T15:00:00+08:00", "2026-06-13T22:00:00+08:00")
    a = cs.actual_targets(k)
    assert a["t8"].isoformat() == "2026-06-14T04:00:00+08:00" and a["t1"].isoformat() == "2026-06-14T11:00:00+08:00"
    # 非例外：T−8h / T−1h 分钟精确
    k2 = _dt("2026-06-13T19:35:00+08:00")
    m2, c2 = cs.rule_targets("2026-06-13", 19, k2, True, has_jc_code=True)
    assert (m2.isoformat(), c2.isoformat()) == ("2026-06-13T11:35:00+08:00", "2026-06-13T18:35:00+08:00")


def test_jingcai_date_from_code_never_from_kickoff_window():
    assert cs.jingcai_date_from_code("六008", _dt("2026-06-14T12:00:00+08:00")) == "2026-06-13"
    assert cs.jingcai_date_from_code("周一002", _dt("2026-10-06T02:45:00+08:00")) == "2026-10-05"
    assert cs.jingcai_date_from_code("周二001", _dt("2026-10-06T19:00:00+08:00")) == "2026-10-06"
    assert cs.jingcai_date_from_code(None, _dt("2026-10-06T19:00:00+08:00")) is None


@pytest.fixture()
def live_copy(tmp_path, monkeypatch):
    p = tmp_path / "app.db"
    src = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    out = sqlite3.connect(str(p))
    src.backup(out)
    src.close()
    out.close()
    monkeypatch.setattr(adb, "DB_PATH", p)
    return p


def _table(client, **kw):
    r = client.get("/table/matches", params={"limit": 500, "scope": "all", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _items_in_span(db_path, client, date_from: str, date_to: str, **kw) -> list:
    """按库里实际竞彩日拆成不超过 31 天的窗口，把原先一次取长区间的结果拼回来。"""
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dates = [date.fromisoformat(r[0]) for r in c.execute(
            "SELECT DISTINCT jingcai_date FROM matches WHERE jingcai_date BETWEEN ? AND ? ORDER BY 1",
            (date_from, date_to))]
    finally:
        c.close()
    items: list = []
    if not dates:
        return items
    start = prev = dates[0]
    windows = []
    for d in dates[1:]:
        if (d - start).days > 31:
            windows.append((start, prev))
            start = d
        prev = d
    windows.append((start, prev))
    for a, b in windows:
        offset = 0
        while True:
            page = _table(client, date_from=a.isoformat(), date_to=b.isoformat(), offset=offset, **kw)
            items.extend(page["items"])
            offset += page["count"]
            if page["count"] == 0 or offset >= page["total"]:
                break
    return items


def test_exception_count_formal_after_merge(live_copy):
    # 2026-10-15 并库之后正式库共 316 场（竞彩 314 场加 2 场非竞彩探针场），其中例外场 241 场；
    # 并库前的 177 场里有 146 场例外场，截止到 2026-07-05 这 177 场的例外场数量不变。
    client = TestClient(app)
    items = _items_in_span(live_copy, client, "2026-01-01", "2026-12-31")
    assert len(items) == 316
    cfg = _table(client, date_from="2026-06-01", date_to="2026-06-01")
    assert cfg["config"]["exception_rule"] == "jc_code_ge_2300"
    assert sum(1 for i in items if i["schedule"]["phase_exception"]) == 241
    old = _items_in_span(live_copy, client, "2026-01-01", "2026-07-05")
    assert len(old) == 177 and sum(1 for i in old if i["schedule"]["phase_exception"]) == 146
    # 并库后正式库有 kickoff_jc 列，这三场的竞彩开赛时间是次日 12:00。
    for uid, jc in (("2026-06-13|六008", "2026-06-14T12:00:00+08:00"), ("2026-06-16|二020", "2026-06-17T12:00:00+08:00"),
                    ("2026-06-20|六036", "2026-06-21T12:00:00+08:00")):
        it = next(i for i in items if i["match_id"] == uid)
        assert it["schedule"]["phase_exception"] is True
        assert it["match"]["kickoff_jc"] == jc


# ---------------- 3. N1 close_unusable / by_close_basis ----------------

def test_close_unusable_snapshot_rules():
    snap = se.build_snapshot(ledger_id="N1", evaluable=False, odds_source="hist",
                             not_evaluable_reason=se.CLOSE_UNUSABLE,
                             not_evaluable_subreason="close_time_unknown_api_closing", close_basis="api_closing")
    assert snap["close_basis"] == "api_closing" and snap["ledger_note"] == se.CLOSE_UNUSABLE_NOTE
    assert se.not_evaluable_of(snap) == ("close_unusable", "close_time_unknown_api_closing")
    with pytest.raises(ValueError):
        se.build_snapshot(ledger_id="N1", evaluable=False, odds_source="hist",
                          not_evaluable_reason=se.CLOSE_UNUSABLE, close_basis="rule_tick")
    with pytest.raises(ValueError):
        se.build_snapshot(ledger_id="N1", evaluable=True, odds_source="hist", close_basis="api_closing")
    with pytest.raises(ValueError):  # N5 不允许 close_unusable
        se.build_snapshot(ledger_id="N5", evaluable=False, odds_source="hist",
                          not_evaluable_reason=se.CLOSE_UNUSABLE, close_basis="api_closing")


def test_close_basis_dimension_and_frozen_n1_inference():
    assert se.DIMENSIONS["close_basis"][1] == ("rule_tick", "api_closing", "unknown")
    assert set(se.DIMENSIONS) == {"odds_source", "open_basis", "close_basis"}
    frozen = {"feature_1x2_book": "william", "source": "5df_odds_snap", "fav": "客"}  # 旧冻结 N1 快照形状
    assert se.close_basis_of(frozen) == "api_closing"
    assert se.close_basis_of({"source": "5df_odds_snap"}) == "unknown"
    assert se.close_basis_of({"close_basis": "rule_tick"}) == "rule_tick"


@pytest.mark.skipif(not V2D3_DB.exists(), reason="v2d3 replica missing")
def test_v2d3_n1_frozen_untouched_and_hist_close_unusable():
    c = sqlite3.connect(f"file:{V2D3_DB}?mode=ro", uri=True)
    try:
        rows = c.execute("SELECT id, direction, rationale_json FROM predictions WHERE strategy='SHADOW_N1'").fetchall()
    finally:
        c.close()
    frozen = [r for r in rows if r[0] in (113, 114)]
    assert len(frozen) == 2 and all(se.not_evaluable_of(r[2]) is None for r in frozen)
    new = [r for r in rows if r[0] not in (113, 114)]
    assert new and all(r[1] == se.NO_BET_DIRECTION for r in new)
    assert {se.not_evaluable_of(r[2]) for r in new} == {("close_unusable", "close_time_unknown_api_closing")}


# ---------------- 5. 欧赔 / 竞彩 water_source ----------------

def test_x1x2_water_source_live(live_copy):
    d = _table(TestClient(app), date_from="2026-06-01", date_to="2026-06-03")
    seen = 0
    for it in d["items"]:
        for b, blk in it["x1x2"].items():
            if b in ("bet365", "avg"):
                continue  # 2026-10-14 新增列，水位来源字段由多家平均模块单独定义
            for ph in ("open", "mid", "close"):
                c = blk[ph]
                assert c["water_source"] in (None, "actual")
                if c["available"]:
                    assert c["water_source"] == "actual"  # 旧手工欧赔逐位录入，已核实非档位值
                    seen += 1
                else:
                    assert c["water_source"] is None
        for ph in ("open", "close"):
            jc = it["jc_1x2"][ph]
            assert jc["water_source"] == ("actual" if jc["available"] else None)
    assert seen > 0
    assert d["config"]["x1x2_return_hl_water"] == "real_only"


def test_x1x2_baseline_real_only(live_copy):
    c = sqlite3.connect(str(live_copy))
    c.execute("UPDATE odds_euro_home SET home_win=home_win")  # no-op；基准只收 actual 的结构性检查见下
    c.commit()
    c.close()
    d = _table(TestClient(app), date_from="2026-06-20", date_to="2026-06-20")
    for it in d["items"]:
        for b, blk in it["x1x2"].items():
            if b in ("bet365", "avg"):
                continue  # 2026-10-14 新增列，水位来源字段由多家平均模块单独定义
            for ph in ("open", "mid", "close"):
                cell = blk[ph]
                if cell["water_source"] != "actual":
                    assert cell["return_rate"] is None or cell["baseline_method"] is not None


# ---------------- 6. §2 真实水位 + tier_cross ----------------

class _FakeD:
    def __init__(self, tiers):
        self.legacy_asian = {(1, "crown", ph): {"extras_json": json.dumps({"water_tier_raw": t})}
                             for ph, t in tiers.items()}


def _cell(line, ws, basis="legacy_import", available=True):
    return {"line": line, "water_source": ws, "basis": basis, "available": available}


def test_water_move_flags_tier_cross():
    """hl_v0.3：只比初盘 → 临盘；档位换算同盘跨档 → close.tier_cross(kind=water_tier)，不上色。"""
    cells = {"open": _cell(0.5, "tier_midpoint"), "mid": _cell(0.75, "tier_midpoint"),
             "close": _cell(0.5, "tier_midpoint")}
    tm.apply_water_move_flags(cells, _FakeD({"open": {"home": 7.5, "away": 1.0},
                                             "mid": {"home": 5.0, "away": 1.0},
                                             "close": {"home": 5.0, "away": 1.0}}), 1, "crown")
    for ph in ("open", "mid"):
        assert cells[ph]["water_move_eligible"] is None and cells[ph]["tier_cross"] is None
    c = cells["close"]
    assert c["water_move_eligible"] is False and c["tier_cross_mid"] is True
    assert c["tier_cross"] == {"kind": "water_tier", "from": {"phase": "open", "line": 0.5, "home": 7.5, "away": 1.0},
                               "to": {"phase": "close", "line": 0.5, "home": 5.0, "away": 1.0}, "sides": ["home"]}


def test_water_move_flags_actual_only():
    cells = {"open": _cell(-0.25, "actual", "first_tick"), "mid": _cell(-0.25, "actual", "rule"),
             "close": _cell(-0.25, None, "rule")}
    tm.apply_water_move_flags(cells, _FakeD({}), 1, "crown")
    assert cells["mid"]["water_move_eligible"] is None
    assert cells["close"]["water_move_eligible"] is False and cells["close"]["tier_cross_mid"] is False


def test_table_flat_columns_stable_with_tier_cross(live_copy):
    r = TestClient(app).get("/table/matches", params={"date_from": "2026-06-01", "date_to": "2026-06-30",
                                                      "format": "flat", "limit": 500})
    assert r.status_code == 200
    items = r.json()["items"]
    keys = {frozenset(i) for i in items}
    assert len(keys) == 1
    k = next(iter(keys))
    assert "ah_crown_close_tier_cross_from_home" in k and "ah_crown_close_water_move_eligible" in k
    assert "ah_crown_close_tier_cross_mid" in k and "ah_crown_close_tier_cross_kind" in k
    assert "ah_crown_mid_fallback_p25" in k and "x1x2_william_open_fallback_n" in k
    assert "x1x2_william_open_water_source" in k
    crosses = [i for i in items if i["ah_crown_close_tier_cross_sides"]]
    assert crosses
    for i in crosses:
        if i["ah_crown_close_tier_cross_kind"] == "line":
            assert i["ah_crown_close_water_move_eligible"] is None and i["ah_crown_close_tier_cross_mid"] is None
        else:
            assert i["ah_crown_close_water_move_eligible"] is False
    assert all(i["ah_crown_mid_water_move_eligible"] is None for i in items)


def test_generator_excludes_tier_water():
    import generate_shadow_predictions as g
    c = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        a = g.load_asian(c)
        b = g.load_asian(c, real_water_only=False)
    finally:
        c.close()
    tier = [k for k, v in b.items() if v.get("water_src") == "tier_midpoint"]
    assert tier
    assert all(a[k]["home_water"] is None and a[k]["water_tier_excluded"] for k in tier)
    assert all(a[k]["handicap"] == b[k]["handicap"] for k in tier)
