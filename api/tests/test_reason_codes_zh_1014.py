"""2026-10-14：原因代码中文说明 + jc_not_on_sale。"""
import csv
import json
import subprocess
import sqlite3
import sys
from pathlib import Path

from app import reason_codes as rc


def test_zh_known_unknown_none():
    assert rc.zh("jc_not_on_sale") == "竞彩未开售胜平负"
    assert rc.zh("too_few_books") == "参与平均的机构少于两家"
    assert rc.zh(None) is None and rc.zh("") is None
    assert rc.zh("xyz") == "未登记的原因：xyz"


def test_annotate_nested_and_none():
    o = {"a": {"null_reason": "no_result", "cell_status_reason": None, "x": 1},
         "l": [{"missing_reason": "collector_empty"}], "result_hidden_reason": "refund", "reasons": ["x"]}
    rc.annotate(o)
    assert o["a"]["null_reason_zh"] == "没有比分或赛果还不可见"
    assert "cell_status_reason_zh" in o["a"] and o["a"]["cell_status_reason_zh"] is None
    assert o["l"][0]["missing_reason_zh"] == "采集端没有数据"
    assert o["result_hidden_reason_zh"] == "竞彩退款场次"
    assert "x_zh" not in o["a"] and "reasons_zh" not in o


def test_all_contract_codes_registered():
    for code in ("not_due", "book_not_open", "no_source", "not_captured", "suspect_truncated", "too_few_books",
                 "collector_empty", "no_result", "special_status", "no_close_odds", "no_goal_line", "jc_not_on_sale",
                 "extra_time_unverified", "no_team_id", "post_match_only", "empty_from_source", "not_collected"):
        assert code in rc.REASON_ZH, code


def _item(uid, had_reason="no_close_odds", available=False):
    return {"match_id": uid,
            "jc_1x2": {"close": {"available": available, "missing_reason": None if available else "collector_empty"},
                       "open": {"available": False, "missing_reason": "collector_empty"}},
            "result": {"jc": {"had": {"null_reason": had_reason}, "hhad": {"null_reason": "collector_empty"}}}}


def test_apply_not_on_sale_only_listed_and_only_empty():
    it = _item("2026-10-13|二008"); rc.apply_jc_not_on_sale(it, {"2026-10-13|二008"})
    assert it["jc_1x2"]["close"]["missing_reason"] == "jc_not_on_sale"
    assert it["jc_1x2"]["open"]["missing_reason"] == "jc_not_on_sale"
    assert it["result"]["jc"]["had"]["null_reason"] == "jc_not_on_sale"
    assert it["result"]["jc"]["hhad"]["null_reason"] == "collector_empty"  # 让球不受影响
    other = _item("2026-10-13|二009"); rc.apply_jc_not_on_sale(other, {"2026-10-13|二008"})
    assert other["result"]["jc"]["had"]["null_reason"] == "no_close_odds"
    # 无比分时保持 no_result；有数据的格子不改
    nr = _item("u", had_reason="no_result", available=True); rc.apply_jc_not_on_sale(nr, {"u"})
    assert nr["result"]["jc"]["had"]["null_reason"] == "no_result"
    assert nr["jc_1x2"]["close"]["missing_reason"] is None


def test_load_missing_file_empty(tmp_path, caplog):
    assert rc.load_jc_not_on_sale(tmp_path / "nope.csv") == set()


def test_load_csv_and_env(tmp_path, monkeypatch):
    p = tmp_path / "j.csv"
    p.write_text("match_uid,sporttery_match_id\n2026-10-09|五006,1\n,2\n", encoding="utf-8")
    monkeypatch.setenv("JC_NOT_ON_SALE_CSV", str(p))
    assert rc.load_jc_not_on_sale() == {"2026-10-09|五006"}


def test_build_script_from_bundle(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "tools"))
    import build_jc_not_on_sale as b
    conn = sqlite3.connect(":memory:")
    conn.execute("create table matches(match_uid text, jc_id text, jingcai_date text)")
    conn.executemany("insert into matches values (?,?,?)",
                     [("2026-10-13|二008", "二008", "2026-10-13"), ("2026-10-10|六009", "六009", "2026-10-10")])
    page = {"value": {"matchResult": [
        {"matchNumStr": "周二008", "matchDate": "2026-10-14", "h": "", "d": "", "a": "", "goalLine": "+3", "matchId": 1, "poolStatus": "", "sectionsNo999": "3:3"},
        {"matchNumStr": "周六009", "matchDate": "2026-10-10", "h": "", "d": "", "a": "", "goalLine": "+2", "matchId": 2, "poolStatus": "", "sectionsNo999": "2:2"},
        {"matchNumStr": "周六006", "matchDate": "2026-10-10", "h": "", "d": "", "a": "", "goalLine": "+1", "matchId": 3, "poolStatus": "Refund", "sectionsNo999": "无效场次"},
        {"matchNumStr": "周二001", "matchDate": "2026-10-14", "h": "1.5", "d": "4", "a": "5", "goalLine": "-1", "matchId": 4, "poolStatus": "Payout", "sectionsNo999": "1:0"}]}}
    rows = b.build({"pages": [json.dumps(page, ensure_ascii=False)], "fetched_at": "t"}, conn)
    assert [r["match_uid"] for r in rows] == ["2026-10-13|二008", "2026-10-10|六009"]


def test_table_nested_top_level_zh_and_not_on_sale(tmp_path, monkeypatch):
    """用 v2d3 只读副本跑一次 build_table：顶层隐藏原因有中文；名单内场次的竞彩胜平负赛果为 jc_not_on_sale。"""
    import pytest
    db = Path("/workspace/match-analysis-api/data/v2d3/app.db")
    if not db.exists():
        pytest.skip("本机没有 v2d3 研究副本")
    from app import table_matches as t
    p = tmp_path / "j.csv"
    p.write_text("match_uid\n2026-10-13|二008\n", encoding="utf-8")
    monkeypatch.setenv("JC_NOT_ON_SALE_CSV", str(p))
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    r = t.build_table(conn, date_from="2026-10-13", date_to="2026-10-13", scope="jc", strategy="CFFXDJ_5_V3",
                      channel="rule", include_live="none", settlement_version=None, as_of=None,
                      baseline_window_days=None, baseline_min_n=t.DEFAULT_BASELINE_MIN_N, limit=500, offset=0)
    it = {i["match_id"]: i for i in r["items"]}["2026-10-13|二008"]
    assert "result_hidden_reason_zh" in it and "hidden_reason_zh" in it
    had = it["result"]["jc"]["had"]
    assert had["null_reason"] == "jc_not_on_sale" and had["null_reason_zh"] == "竞彩未开售胜平负"
    assert it["jc_1x2"]["close"]["missing_reason_zh"] == "竞彩未开售胜平负"
    assert "prediction" not in it or not any(k.endswith("_zh") for k in (it["prediction"] or {}))


# 2026-10-14：接口可能返回的原因代码都必须有中文说明
API_REASON_CODES = (
    # 盘口格子、平均格子、剔除原因
    "not_due", "book_not_open", "no_source", "not_captured", "suspect_truncated", "too_few_books",
    "deviation_too_large", "conversion_failed",
    # 竞彩格子与赛果
    "collector_empty", "legacy_home_only", "jc_1x2_incomplete", "jc_not_on_sale", "probe_not_sporttery_official",
    "no_result", "special_status", "no_close_odds", "no_goal_line", "jc_official_first_quote_missing",
    "no_line_visible_at_decision",
    # 隐藏原因
    "no_data", "after_as_of", "before_kickoff", "not_visible_yet", "kickoff_unknown", "refund", "postponed",
    "cancelled", "abandoned", "no_prediction", "produced_after_as_of", "no_settle_line", "bad_direction",
    # 初盘依据与截断检查
    "no_open_data", "open_time_unknown_after_decision_possible", "probe_match", "no_group_for_book",
    "jc_no_quantile_group", "quantile_table_missing", "first_record_or_kickoff_unknown",
    # 十一点十分主值来源
    "own_capture_out_of_window", "own_capture_out_of_window;no_timeline",
    "ah_sign_mismatch_probe",
    # 基本面
    "no_team_id", "extra_time_unverified", "post_match_only", "empty_from_source", "not_collected",
    "source_not_covered", "no_standings", "no_pinnacle_1x2", "injury_list_empty_unconfirmed",
    "home_not_due", "away_not_due",
    # 影子策略不可评估子原因
    "fit_not_converged", "real_water_lt_3", "stale_tick",
)


def test_every_api_reason_code_has_zh():
    missing = [c for c in API_REASON_CODES if c not in rc.REASON_ZH]
    assert not missing, missing
    for c in API_REASON_CODES:
        assert rc.REASON_ZH[c].strip() and not rc.zh(c).startswith("未登记的原因")


def test_source_literal_reasons_registered():
    """扫描接口代码里直接写死的原因字面值，防止以后新增代码忘记登记。"""
    import re
    root = Path(__file__).resolve().parents[1] / "app"
    pat = re.compile(r'''["'](\w*reason)["']\s*\]?\s*[:=]\s*["']([a-z0-9_;]+)["']''')
    status_values = {"ok", "insufficient", "unavailable", "pending"}
    missing = []
    for f in ("table_matches.py", "avg_cells.py", "open_status.py", "avg_conversion.py"):
        for line in (root / f).read_text(encoding="utf-8").splitlines():
            for m in pat.finditer(line):
                v = m.group(2)
                if v not in status_values and v not in rc.REASON_ZH:
                    missing.append((f, v))
    assert not missing, missing


def test_status_values_not_registered_as_reasons():
    for s in ("ok", "insufficient", "unavailable"):
        assert s not in rc.REASON_ZH
