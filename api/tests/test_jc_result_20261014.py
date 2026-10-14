"""2026-10-14：竞彩胜平负与让球胜平负赛果字段 jc_result（按比分判定，带出临盘对应赔率）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import table_matches as tm  # noqa: E402

R00 = {"status": "finished", "home_goals": 0, "away_goals": 0}
R21 = {"status": "finished", "home_goals": 2, "away_goals": 1}
X = {"close": {"home": 2.25, "draw": 3.32, "away": 2.62}}


def test_had_draw_and_win():
    assert tm.build_jc_result(R00, None, X, None)["had"]["outcome_zh"] == "平"
    r = tm.build_jc_result(R00, None, X, None)["had"]
    assert r["odds"] == 3.32 and r["null_reason"] is None
    r = tm.build_jc_result(R21, None, {"close": {"home": 3.34, "draw": 3.1, "away": 1.97}}, None)["had"]
    assert (r["outcome"], r["odds"]) == ("home", 3.34)


def test_hhad_adjusts_by_goal_line():
    h = {"close": {"goal_line": -1, "home": 3.9, "draw": 3.5, "away": 1.7}}
    r = tm.build_jc_result(R21, None, X, h)["hhad"]
    assert (r["outcome_zh"], r["odds"], r["adjusted_score"]) == ("平", 3.5, "1-1")
    h = {"close": {"goal_line": 1, "home": 1.5, "draw": 3.5, "away": 5}}
    assert tm.build_jc_result(R00, None, X, h)["hhad"]["outcome"] == "home"


def test_null_reasons():
    assert tm.build_jc_result(None, "refund", X, None)["had"]["null_reason"] == "special_status"
    assert tm.build_jc_result(None, "no_result", X, None)["had"]["null_reason"] == "no_result"
    assert tm.build_jc_result(R00, None, {"close": {"home": 2.0}}, None)["had"]["null_reason"] == "no_close_odds"
    r = tm.build_jc_result(R00, None, X, {"close": {"goal_line": None, "home": None}})["hhad"]
    assert r["null_reason"] == "no_close_odds" and r["outcome"] is None
    r = tm.build_jc_result(R00, None, X, {"close": {"goal_line": None, "home": 1, "draw": 2, "away": 3}})["hhad"]
    assert r["null_reason"] == "no_goal_line"


def test_hhad_collector_empty_reason():
    h = {"close": {"goal_line": None, "home": None, "missing_reason": "collector_empty"}}
    assert tm.build_jc_result(R00, None, X, h)["hhad"]["null_reason"] == "collector_empty"
