"""基本面宽表：stats 批量组装，以及临盘欧赔人气差值。"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import build_stats, load_stats_many  # noqa: E402
from app.popularity_diff import (  # noqa: E402
    diff_from_1x2,
    popularity_from_prices,
    project_table_stats,
)


def _close(home, draw, away, *, status="ok", recorded_at=None):
    return {"home": home, "draw": draw, "away": away, "cell_status": status, "recorded_at": recorded_at}


def test_diff_is_home_minus_away_implied():
    # 1.5 / 4 / 6 → 主胜概率明显高于客胜
    value = diff_from_1x2(1.5, 4.0, 6.0)
    assert value is not None and value > 0
    assert diff_from_1x2(6.0, 4.0, 1.5) == -value
    assert diff_from_1x2(1.0, 3.0, 4.0) is None
    assert diff_from_1x2(2.0, None, 3.0) is None


def test_average_when_two_books_else_fallback_order():
    averaged = popularity_from_prices({
        "crown": (2.2, 3.2, 3.4),
        "bet365": (2.0, 3.0, 3.6),
    })
    assert averaged["source"] == "close_euro_avg"
    assert averaged["books"] == ["crown", "bet365"]
    mean = diff_from_1x2(2.1, 3.1, 3.5)
    assert averaged["value"] == mean

    only_crown = popularity_from_prices({"crown": (2.2, 3.2, 3.4), "pinnacle": (2.0, None, 3.0)})
    assert only_crown["source"] == "close_euro_crown"
    assert only_crown["books"] == ["crown"]

    pin_first = popularity_from_prices({
        "bet365": (1.8, 3.4, 4.2),
        "pinnacle": (1.9, 3.5, 4.0),
    })
    # 两家齐全走平均，不因为平博在回退名单里就改用单家
    assert pin_first["source"] == "close_euro_avg"

    single = popularity_from_prices({"william": (1.7, 3.6, 5.0), "macau": (1.0, 3.0, 4.0)})
    assert single["source"] == "close_euro_william"
    assert popularity_from_prices({}) is None


def test_project_keeps_stored_and_fills_gap_from_visible_close():
    blocks = {
        "avg": {"close": {**_close(2.0, 3.2, 3.8), "avg_detail": {
            "averaged_books": ["pinnacle", "william"],
            "sources": {"pinnacle": {"recorded_at": "2026-10-18T18:00:00+08:00"}},
        }}},
        "pinnacle": {"close": _close(1.2, None, 5.0, status="unavailable")},
    }
    kept = project_table_stats(
        {"popularity_diff": 0.053, "support_proxy_odds": 0.2, "recent": {"home_streak_last6": 1}},
        blocks,
    )
    assert kept["popularity_diff"] == 0.053
    assert "support_proxy_odds" not in kept
    assert kept["recent"]["home_streak_last6"] == 1

    filled = project_table_stats({"popularity_diff": None, "rank": {"home": 5, "away": None}}, blocks)
    assert filled["popularity_diff"] == diff_from_1x2(2.0, 3.2, 3.8)
    assert filled["meta"]["popularity"]["source"] == "close_euro_avg"
    assert filled["meta"]["popularity"]["as_of"] == "2026-10-18T18:00:00+08:00"
    assert filled["rank"]["home"] == 5

    fallback = project_table_stats({"popularity_diff": None}, {
        "avg": {"close": _close(None, None, None, status="insufficient")},
        "macau": {"close": _close(1.8, 3.3, 4.4)},
        "bet365": {"close": _close(1.7, 3.4, 4.6)},
    })
    # 平均不足，平博、威廉都没有，下一档是澳门
    assert fallback["meta"]["popularity"]["source"] == "close_euro_macau"
    assert fallback["popularity_diff"] == diff_from_1x2(1.8, 3.3, 4.4)

    hidden = project_table_stats(None, {
        "pinnacle": {"close": _close(None, None, None, status="unavailable")},
    })
    assert hidden["popularity_diff"] is None
    assert hidden["injury"] is None
    assert hidden["weather"] is None


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE stats (
            match_id INTEGER PRIMARY KEY,
            recent_json TEXT, home_streak_last6 REAL, h2h_json TEXT,
            rank_home INTEGER, rank_away INTEGER, popularity_diff REAL,
            injury_json TEXT, weather_json TEXT, extras_json TEXT,
            source_recent TEXT, as_of_recent TEXT,
            source_injury TEXT, as_of_injury TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE stats_obs (
            id INTEGER PRIMARY KEY,
            match_id INTEGER, kind TEXT, as_of TEXT, source TEXT,
            payload_json TEXT, quality TEXT, fetched_at TEXT
        )"""
    )
    return conn


def test_load_stats_many_matches_build_stats_and_does_not_invent_known_empty():
    conn = _conn()
    conn.execute(
        "INSERT INTO stats (match_id, recent_json, home_streak_last6, h2h_json, rank_home, rank_away, "
        "popularity_diff, injury_json, weather_json, extras_json, source_recent, as_of_recent) "
        "VALUES (1, ?, 2, ?, 5, 12, 0.053, NULL, ?, ?, 'manual_seed', '2026-06-01T12:00:00+08:00')",
        (
            json.dumps({"last10": {"home": {"gf": 15, "ga": 5}, "away": {"gf": 8, "ga": 11}}}),
            json.dumps({"matches": 6, "last6": {"home_gf": 10, "home_ga": 8}, "home_streak_last6": 1}),
            json.dumps("下雨"),
            json.dumps({"support_proxy_odds": 0.12}),
        ),
    )
    conn.execute(
        "INSERT INTO stats_obs (match_id, kind, as_of, source, payload_json, quality, fetched_at) VALUES "
        "(2, 'injury', '2026-06-02T10:00:00+08:00', 'bsd', ?, 'ok', '2026-06-02T10:00:00+08:00')",
        (json.dumps({"home": [], "away": [], "known_empty": True}),),
    )
    conn.execute(
        "INSERT INTO stats (match_id, recent_json, injury_json, weather_json) VALUES (2, NULL, NULL, NULL)"
    )
    conn.commit()

    one = build_stats(conn, 1)
    many = load_stats_many(conn, [1, 2, 3])
    assert many[1] == one
    assert many[1]["recent"]["last10"]["home"] == {"gf": 15, "ga": 5}
    assert many[1]["h2h"]["matches"] == 6
    assert many[1]["rank"] == {"home": 5, "away": 12}
    assert many[1]["popularity_diff"] == 0.053
    assert many[1]["weather"] == "下雨"
    assert many[1]["injury"] is None
    assert many[1]["support_proxy_odds"] == 0.12
    assert many[1]["meta"]["recent"]["source"] == "manual_seed"
    assert "obs" in many[1] and many[1]["obs"] == {}
    assert 3 not in many

    assert many[2]["injury"] is None
    assert many[2]["obs"]["injury"]["known_empty"] is True
    projected = project_table_stats(many[2], {})
    assert projected["obs"]["injury"]["known_empty"] is True
    assert "support_proxy_odds" not in project_table_stats(many[1], {})


def _fill_mod():
    path = ROOT / "scripts" / "fill_popularity_diff.py"
    spec = importlib.util.spec_from_file_location("fill_popularity_diff", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fill_script_uses_snapshot_close_and_skips_stored():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE stats (
            match_id INTEGER PRIMARY KEY, popularity_diff REAL,
            source_popularity TEXT, as_of_popularity TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE odds_snapshot (
            match_id INTEGER, book TEXT, market TEXT, channel TEXT, point TEXT,
            price_home REAL, price_draw REAL, price_away REAL,
            recorded_at TEXT, extras_json TEXT
        )"""
    )
    conn.execute("CREATE TABLE odds_euro_home (match_id INTEGER, book TEXT, phase TEXT, home_win REAL)")
    conn.execute("INSERT INTO stats (match_id, popularity_diff) VALUES (1, NULL), (2, 0.053)")
    conn.executemany(
        "INSERT INTO odds_snapshot VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "pinnacle", "euro_1x2", "rule", "close", 1.9, 3.4, 4.1, "2026-10-18T18:00:00+08:00", None),
            (1, "william", "euro_1x2", "rule", "close", 1.95, 3.3, 4.0, "2026-10-18T18:05:00+08:00", None),
            (1, "macau", "euro_1x2", "rule", "close", 1.5, 3.0, 6.0, "2026-10-18T18:06:00+08:00",
             json.dumps({"api_phase": "closing"})),
            (1, "crown", "euro_1x2", "rule", "open", 2.2, 3.2, 3.4, "2026-10-18T11:10:00+08:00", None),
            (2, "pinnacle", "euro_1x2", "rule", "close", 1.4, 4.0, 7.0, "2026-10-18T18:00:00+08:00", None),
        ],
    )
    conn.execute("INSERT INTO odds_euro_home VALUES (1, 'pinnacle', 'close', 9.9)")
    conn.commit()
    mod = _fill_mod()
    pending = mod.plan_updates(conn)
    assert [row[0] for row in pending] == [1]
    assert pending[0][2] == "close_euro_avg"
    # 收盘参考价与初盘、只有主胜的旧表都不进平均
    assert pending[0][1] == popularity_from_prices({
        "pinnacle": (1.9, 3.4, 4.1),
        "william": (1.95, 3.3, 4.0),
    })["value"]
    assert mod.apply_updates(conn, pending) == 1
    assert pending[0][3] == "2026-10-18T18:05:00+08:00"
    assert conn.execute("SELECT popularity_diff FROM stats WHERE match_id=1").fetchone()[0] == pending[0][1]
    assert conn.execute("SELECT source_popularity FROM stats WHERE match_id=1").fetchone()[0] == "close_euro_avg"
    assert conn.execute("SELECT as_of_popularity FROM stats WHERE match_id=1").fetchone()[0] == pending[0][3]
    assert conn.execute("SELECT popularity_diff FROM stats WHERE match_id=2").fetchone()[0] == 0.053
