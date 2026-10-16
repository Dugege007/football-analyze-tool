"""2026-10-16：预测／回测／切片默认结算盘为平博临盘优先，缺平博再用澳门临盘。

显式 macau_close 只走澳门。不改 predictions 行，也不走 market_settle。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import backtest as bt  # noqa: E402
from app import main as m  # noqa: E402
from app import settle_book as sb  # noqa: E402
from app import table_matches as tm  # noqa: E402


def _book(handicap, *, home=0.9, away=0.9, water_src="actual", censored_home=0, censored_away=0):
    return {
        "handicap": handicap,
        "home_water": home,
        "away_water": away,
        "water_src": water_src,
        "censored_home": censored_home,
        "censored_away": censored_away,
    }


def _getter(table):
    def getter(book, phase):
        return table.get((book, phase))
    return getter


def test_priority_constant_and_version():
    assert sb.DEFAULT_SETTLE_BOOK == "pinnacle_then_macau"
    assert sb.SETTLE_BOOK_PRIORITY == ("pinnacle_close", "macau_close")
    assert sb.SETTLEMENT_BOOK_VERSION == bt.SETTLEMENT_BOOK_VERSION
    assert bt.SETTLEMENT_VERSION == "ah_v4_water_midpoint"
    assert m.ValidateRequest().settle_book == sb.DEFAULT_SETTLE_BOOK
    assert m.CompareRequest().settle_book == sb.DEFAULT_SETTLE_BOOK
    assert m.normalize_strategy_config({})["settle_book"] == sb.DEFAULT_SETTLE_BOOK


def test_default_uses_pinnacle_when_close_exists():
    table = {
        ("pinnacle", "close"): _book(0.25, home=0.88),
        ("macau", "close"): _book(-0.5, home=1.02),
    }
    for token in (None, "", sb.DEFAULT_SETTLE_BOOK, "pinnacle_close>macau_close"):
        got = sb.resolve_settle_quote(_getter(table), token)
        assert got.settled_with == "pinnacle", token
        assert got.fallback_book is None
        assert got.handicap == 0.25
        assert got.priority is True


def test_level_ball_counts_as_pinnacle_close():
    table = {("pinnacle", "close"): _book(0.0), ("macau", "close"): _book(0.5)}
    got = sb.resolve_settle_quote(_getter(table), None)
    assert got.settled_with == "pinnacle" and got.handicap == 0.0 and got.fallback_book is None


def test_missing_pinnacle_falls_back_to_macau():
    table = {("macau", "close"): _book(-0.25, home=1.1)}
    got = sb.resolve_settle_quote(_getter(table), sb.DEFAULT_SETTLE_BOOK)
    assert got.settled_with == "macau"
    assert got.fallback_book == "macau"
    assert got.handicap == -0.25


def test_pinnacle_row_without_line_falls_back():
    table = {
        ("pinnacle", "close"): _book(None),
        ("macau", "close"): _book(0.75),
    }
    got = sb.resolve_settle_quote(_getter(table), None)
    assert got.settled_with == "macau" and got.fallback_book == "macau" and got.handicap == 0.75


def test_explicit_macau_close_ignores_pinnacle():
    table = {
        ("pinnacle", "close"): _book(0.25),
        ("macau", "close"): _book(-0.5),
    }
    got = sb.resolve_settle_quote(_getter(table), "macau_close")
    assert got.priority is False
    assert got.settled_with == "macau" and got.fallback_book is None and got.handicap == -0.5
    assert sb.policy_fields("macau_close") == {
        "settle_book_priority": None,
        "settlement_book_version": None,
    }


def test_explicit_pinnacle_close_does_not_fall_back():
    table = {("macau", "close"): _book(-0.5)}
    got = sb.resolve_settle_quote(_getter(table), "pinnacle_close")
    assert got.settled_with is None and got.fallback_book is None and got.handicap is None
    assert m._parse_settle_book("crown_close") == ("crown", "close")
    assert m._parse_settle_book("macau_close") == ("macau", "close")
    with pytest.raises(ValueError):
        m._parse_settle_book(sb.DEFAULT_SETTLE_BOOK)


def test_neither_book_has_no_quote():
    got = sb.resolve_settle_quote(_getter({}), None)
    assert got.settled_with is None and got.fallback_book is None


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE matches (
          id INTEGER PRIMARY KEY, jingcai_date TEXT, jc_no INTEGER, match_uid TEXT,
          jc_id TEXT, kickoff_at TEXT, home_team TEXT, away_team TEXT,
          home_team_id INTEGER, away_team_id INTEGER, scope TEXT
        );
        CREATE TABLE teams (id INTEGER PRIMARY KEY, name_zh_canonical TEXT);
        CREATE TABLE results (match_id INTEGER, home_goals INTEGER, away_goals INTEGER);
        CREATE TABLE predictions (
          match_id INTEGER, strategy TEXT, direction TEXT, stake INTEGER,
          rationale_json TEXT, settle_book TEXT
        );
        CREATE TABLE odds_asian (
          match_id INTEGER, book TEXT, phase TEXT, handicap REAL,
          home_water REAL, away_water REAL, water_src TEXT, extras_json TEXT
        );
        """
    )
    return c


def _match(c, mid, *, uid):
    c.execute(
        "INSERT INTO matches (id, jingcai_date, jc_no, match_uid, jc_id, scope, home_team, away_team)"
        " VALUES (?, '2026-10-16', ?, ?, ?, 'jingcai', '主', '客')",
        (mid, mid, uid, uid),
    )
    c.execute("INSERT INTO results (match_id, home_goals, away_goals) VALUES (?, 2, 0)", (mid,))


def _odd(c, mid, book, handicap, home, away=0.9):
    c.execute(
        "INSERT INTO odds_asian (match_id, book, phase, handicap, home_water, away_water, water_src, extras_json)"
        " VALUES (?, ?, 'close', ?, ?, ?, 'actual', '{}')",
        (mid, book, handicap, home, away),
    )


def test_backtest_default_priority_and_explicit_macau_do_not_rewrite_rows():
    c = _conn()
    _match(c, 1, uid="m1")
    _match(c, 2, uid="m2")
    _odd(c, 1, "pinnacle", 0.25, 0.88)
    _odd(c, 1, "macau", -0.5, 1.10)
    _odd(c, 2, "macau", 0.0, 1.02)
    c.execute(
        "INSERT INTO predictions (match_id, strategy, direction, stake, settle_book) VALUES (1, 'S', '主', 1, 'macau_close')"
    )
    c.execute(
        "INSERT INTO predictions (match_id, strategy, direction, stake, settle_book) VALUES (2, 'S', '主', 1, 'macau_close')"
    )
    c.commit()
    bets, skipped = m._load_strategy_bets(
        c, strategy_key="S", settle_book=sb.DEFAULT_SETTLE_BOOK, scope="jingcai",
        stake_rule={"default_units": 1, "use_prediction_stake": True, "below_min": "raise"},
        fallback_juice=0.95,
    )
    by_id = {b["match_id"]: b for b in bets}
    assert skipped == {"no_result": 0, "no_odds": 0, "bad_direction": 0}
    assert by_id[1]["settled_with"] == "pinnacle" and by_id[1]["fallback_book"] is None
    assert by_id[1]["handicap"] == 0.25
    assert by_id[2]["settled_with"] == "macau" and by_id[2]["fallback_book"] == "macau"
    assert by_id[2]["handicap"] == 0.0
    # 冻结行里的 settle_book 不被回测改写
    stored = [r[0] for r in c.execute("SELECT settle_book FROM predictions ORDER BY match_id")]
    assert stored == ["macau_close", "macau_close"]

    rules = {
        "initial_bankroll": 10000, "unit_fraction": 0.005, "per_match": 10, "per_day": 10,
        "min_stake": None, "max_pct": None, "below_min": "skip", "book": "pinnacle",
        "settlement_version": bt.SETTLEMENT_VERSION, "exclude_censored": False,
    }
    sim = bt.simulate(bets, rules=rules, strategy_order=["S"])
    juice = {e["match_id"]: (e["juice_used"], e["juice_source"]) for e in sim["entries"]}
    assert juice[1] == (0.88, "actual")
    assert juice[2] == (0.95, "fixed_macau")

    macau_bets, _ = m._load_strategy_bets(
        c, strategy_key="S", settle_book="macau_close", scope="jingcai",
        stake_rule={"default_units": 1, "use_prediction_stake": True, "below_min": "raise"},
        fallback_juice=0.95,
    )
    macau_by = {b["match_id"]: b for b in macau_bets}
    assert macau_by[1]["settled_with"] == "macau" and macau_by[1]["fallback_book"] is None
    assert macau_by[1]["handicap"] == -0.5
    assert macau_by[2]["fallback_book"] is None


def test_slice_settlement_honors_frozen_macau_and_defaults_when_blank():
    class Data:
        legacy_asian = {
            (7, "pinnacle", "close"): _book(0.25, home=0.8),
            (7, "macau", "close"): _book(-0.25, home=1.05),
        }

    res = {"home_goals": 2, "away_goals": 0}
    frozen, reason = tm._settle(
        {"direction": "主", "stake": 1, "settle_book": "macau_close"},
        res, Data(), 7, bt.SETTLEMENT_VERSION,
    )
    assert reason is None
    assert frozen["settle_book"] == "macau_close"
    assert frozen["settled_with"] == "macau" and frozen["fallback_book"] is None
    assert frozen["line"] == -0.25 and frozen["juice_source"] == "fixed_macau" and frozen["juice"] == 0.95

    default, _ = tm._settle(
        {"direction": "主", "stake": 1, "settle_book": None},
        res, Data(), 7, bt.SETTLEMENT_VERSION,
    )
    assert default["settle_book"] == sb.DEFAULT_SETTLE_BOOK
    assert default["settled_with"] == "pinnacle" and default["fallback_book"] is None
    assert default["line"] == 0.25 and default["juice_source"] == "actual"

    only_macau = Data()
    only_macau.legacy_asian = {(7, "macau", "close"): _book(0.5, home=1.2)}
    fallback, _ = tm._settle(
        {"direction": "主", "stake": 1, "settle_book": ""},
        res, only_macau, 7, bt.SETTLEMENT_VERSION,
    )
    assert fallback["settled_with"] == "macau" and fallback["fallback_book"] == "macau"
    assert fallback["juice_source"] == "fixed_macau"


def test_prediction_line_follows_row_book_or_default():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE strategy_defs (strategy_key TEXT, status TEXT, config_json TEXT);
        CREATE TABLE predictions (
          match_id INTEGER, strategy TEXT, direction TEXT, stake INTEGER,
          rationale_json TEXT, settle_book TEXT
        );
        CREATE TABLE prediction_legs (
          id INTEGER PRIMARY KEY, match_id INTEGER, market TEXT, side TEXT,
          line REAL, line_text TEXT, stake REAL, strategy TEXT, status TEXT
        );
        CREATE TABLE odds_asian (match_id INTEGER, book TEXT, phase TEXT, handicap REAL);
        INSERT INTO strategy_defs VALUES ('FORMAL_A', 'active', '{}');
        INSERT INTO odds_asian VALUES (1, 'macau', 'close', 2.0);
        INSERT INTO odds_asian VALUES (1, 'pinnacle', 'close', -0.25);
        INSERT INTO predictions VALUES (1, 'FORMAL_A', '主', 1, '[]', 'macau_close');
        INSERT INTO predictions VALUES (1, 'FORMAL_A', '客', 1, '[]', NULL);
        """
    )
    # 同一正式方案两行：写死澳门的仍用澳门盘口；空 settle_book 用平博。
    picks = m.formal_prediction_picks(c, 1)
    by_side = {p["side"]: p["line"] for p in picks if p["market"] == "ah"}
    assert by_side == {"主": 2.0, "客": -0.25}
