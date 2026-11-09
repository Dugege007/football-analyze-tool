"""赛程缺口：掉出列表记其他，不猜取消；新开赛时间记推迟；完整性与按时间模拟。"""
from __future__ import annotations

import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import backtest as bt
from app import schedule_status as ss


def test_dropped_from_list_is_other_not_cancelled():
    status, evidence = ss.classify_schedule(
        source_status="scheduled",
        absent_from_list=True,
        kickoff_changed=False,
        delta=None,
        placeholder_confirm=False,
        previous_status=None,
        previous_evidence=None,
    )
    assert status == "other"
    assert evidence == ss.EVIDENCE_DROPPED
    note = ss.schedule_note_zh(status) or ""
    assert note.startswith("已从竞彩列表消失")
    assert "不能确定" in note
    assert status != "cancelled"


def test_explicit_cancel_and_postpone_are_not_guessed_from_a_drop():
    status, evidence = ss.classify_schedule(
        source_status="cancelled",
        absent_from_list=True,
        kickoff_changed=False,
        delta=None,
        placeholder_confirm=False,
        previous_status=None,
        previous_evidence=None,
    )
    assert (status, evidence) == ("cancelled", ss.EVIDENCE_CANCELLED)
    status, evidence = ss.classify_schedule(
        source_status="postponed",
        absent_from_list=False,
        kickoff_changed=False,
        delta=None,
        placeholder_confirm=False,
        previous_status=None,
        previous_evidence=None,
    )
    assert (status, evidence) == ("postponed", ss.EVIDENCE_POSTPONED)
    assert ss.schedule_note_zh("postponed") == "比赛推迟，新的开赛时间尚未确认"


def test_fifteen_minute_drift_is_not_a_postpone():
    status, evidence = ss.classify_schedule(
        source_status=None,
        absent_from_list=False,
        kickoff_changed=True,
        delta=timedelta(minutes=15),
        placeholder_confirm=False,
        previous_status=None,
        previous_evidence=None,
    )
    assert status is None and evidence is None
    assert ss.is_unannounced_drift(timedelta(minutes=20))
    assert not ss.is_unannounced_drift(timedelta(minutes=21))


def test_reschedule_names_the_new_kickoff_and_return_clears_a_stale_drop():
    status, evidence = ss.classify_schedule(
        source_status=None,
        absent_from_list=False,
        kickoff_changed=True,
        delta=timedelta(hours=44),
        placeholder_confirm=False,
        previous_status="other",
        previous_evidence=ss.EVIDENCE_DROPPED,
    )
    assert (status, evidence) == ("postponed", ss.EVIDENCE_RESCHEDULED)
    note = ss.schedule_note_zh(
        "postponed",
        "2026-11-09T19:00:00+08:00",
        "2026-11-07T23:00:00+08:00",
    )
    assert note == "推迟至 2026-11-09 19:00（北京时间）"
    cleared, cleared_ev = ss.classify_schedule(
        source_status=None,
        absent_from_list=False,
        kickoff_changed=False,
        delta=None,
        placeholder_confirm=False,
        previous_status="other",
        previous_evidence=ss.EVIDENCE_DROPPED,
    )
    assert cleared is None and cleared_ev is None


def test_listing_keeps_original_order_and_echoes_on_the_new_day():
    def row(uid, jd, jc_no, kickoff, status=None, original=None, actual=None):
        return {
            "match_id": uid,
            "match": {
                "jingcai_date": jd,
                "jc_no": jc_no,
                "jc_id": f"六{jc_no:03d}" if jd.endswith("07") else f"一{jc_no:03d}",
                "kickoff_at": actual or kickoff,
                "kickoff_original": original,
                "kickoff_actual": actual,
                "schedule_status": status,
                "schedule_note": ss.schedule_note_zh(status, actual, original),
            },
        }

    source = [
        row("2026-11-07|六001", "2026-11-07", 1, "2026-11-07T18:00:00+08:00"),
        row("2026-11-07|六018", "2026-11-07", 18, "2026-11-07T23:00:00+08:00",
            status="postponed", original="2026-11-07T23:00:00+08:00",
            actual="2026-11-09T19:00:00+08:00"),
        row("2026-11-07|六019", "2026-11-07", 19, "2026-11-07T23:30:00+08:00"),
        row("2026-11-09|一001", "2026-11-09", 1, "2026-11-09T15:00:00+08:00"),
        row("2026-11-09|一002", "2026-11-09", 2, "2026-11-09T21:00:00+08:00"),
    ]
    items = ss.expand_listings(source, date_from="2026-11-07", date_to="2026-11-09")
    monday = [it for it in items if it["match"]["list_date"] == "2026-11-07"]
    assert [it["match_id"] for it in monday] == [
        "2026-11-07|六001", "2026-11-07|六018", "2026-11-07|六019",
    ]
    assert all(it["match"]["listing_role"] == "ticket" for it in monday)
    later = [it for it in items if it["match"]["list_date"] == "2026-11-09"]
    assert [it["row_id"] for it in later] == [
        "2026-11-09|一001", "2026-11-07|六018#kickoff", "2026-11-09|一002",
    ]
    echo = later[1]
    assert echo["match"]["listing_role"] == "kickoff"
    assert echo["match"]["schedule_note"] == "推迟至 2026-11-09 19:00（北京时间）"
    # 只看新开赛那天时，原竞彩日那一行不在结果里，推迟场仍出现。
    only_later = ss.expand_listings(source, date_from="2026-11-09", date_to="2026-11-09")
    assert "2026-11-07|六018#kickoff" in [it["row_id"] for it in only_later]
    assert "2026-11-07|六018" not in [it["row_id"] for it in only_later]


def test_same_day_postpone_is_not_listed_twice():
    items = ss.expand_listings([{
        "match_id": "2026-07-05|日092",
        "match": {
            "jingcai_date": "2026-07-05",
            "jc_no": 92,
            "kickoff_at": "2026-07-06T09:00:00+08:00",
            "kickoff_original": "2026-07-06T08:00:00+08:00",
            "kickoff_actual": "2026-07-06T09:00:00+08:00",
            "schedule_status": "postponed",
        },
    }], date_from="2026-07-05", date_to="2026-07-06")
    assert len(items) == 1
    assert items[0]["match"]["listing_role"] == "ticket"


def test_close_completeness_ignores_classified_gaps_only():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY, match_uid TEXT, scope TEXT, jingcai_date TEXT, schedule_status TEXT
        );
        CREATE TABLE odds_snapshot (
            id INTEGER PRIMARY KEY, match_id INTEGER, book TEXT, market TEXT, channel TEXT, point TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO matches (id, match_uid, scope, jingcai_date, schedule_status) VALUES (?,?,?,?,?)",
        [
            (1, "2026-11-07|六001", "jingcai", "2026-11-07", None),
            (2, "2026-11-07|六018", "jingcai", "2026-11-07", "other"),
            (3, "2026-11-07|六019", "jingcai", "2026-11-07", "cancelled"),
            (4, "2026-11-07|六020", "jingcai", "2026-11-07", "postponed"),
        ],
    )
    conn.execute(
        "INSERT INTO odds_snapshot (match_id, book, market, channel, point) VALUES (1, 'pinnacle', 'asian', 'rule', 'close')"
    )
    report = ss.jingcai_day_close_report(conn, "2026-11-07")
    assert report["complete"] is True
    assert {x["match_uid"] for x in report["exempt"]} == {
        "2026-11-07|六018", "2026-11-07|六019", "2026-11-07|六020",
    }
    conn.execute("INSERT INTO matches (id, match_uid, scope, jingcai_date, schedule_status) VALUES (5,?,?,?,?)",
                 ( "2026-11-07|六021", "jingcai", "2026-11-07", None))
    blocked = ss.jingcai_day_close_report(conn, "2026-11-07")
    assert blocked["complete"] is False
    assert [x["match_uid"] for x in blocked["missing"]] == ["2026-11-07|六021"]


def _bet(uid, jingcai_date, jc_no, kickoff, actual=None):
    return {
        "strategy_key": "S",
        "match_id": jc_no,
        "match_uid": uid,
        "jingcai_date": jingcai_date,
        "order_key": jc_no,
        "direction": "主",
        "pred_stake": 1,
        "stake_rule": {},
        "home_goals": 1,
        "away_goals": 0,
        "handicap": 0.0,
        "home_water": 0.9,
        "away_water": 0.9,
        "fallback_juice": 0.95,
        "kickoff_at": kickoff,
        "kickoff_actual": actual,
    }


_RULES = {
    "initial_bankroll": 10000.0,
    "unit_fraction": 0.01,
    "per_match": 10,
    "per_day": 1,
    "min_stake": None,
    "max_pct": None,
    "below_min": "skip",
}


def test_published_settlement_order_stays_on_jingcai_day():
    bets = [
        _bet("2026-11-07|六018", "2026-11-07", 18, "2026-11-07T23:00:00+08:00",
             "2026-11-09T19:00:00+08:00"),
        _bet("2026-11-08|日001", "2026-11-08", 1, "2026-11-08T15:00:00+08:00"),
    ]
    published = bt.simulate(bets, rules=_RULES, strategy_order=["S"])
    assert [e["match_uid"] for e in published["entries"]] == [
        "2026-11-07|六018", "2026-11-08|日001",
    ]
    assert all("per_day" not in e["reasons"] for e in published["entries"])


def test_paper_walk_uses_effective_kickoff_and_that_day_cap():
    bets = [
        _bet("2026-11-07|六018", "2026-11-07", 18, "2026-11-07T23:00:00+08:00",
             "2026-11-09T19:00:00+08:00"),
        _bet("2026-11-09|一001", "2026-11-09", 1, "2026-11-09T15:00:00+08:00"),
        _bet("2026-11-08|日001", "2026-11-08", 1, "2026-11-08T15:00:00+08:00"),
    ]
    walked = bt.simulate(bets, rules=ss.paper_walk_rules(_RULES), strategy_order=["S"])
    assert [e["match_uid"] for e in walked["entries"]] == [
        "2026-11-08|日001", "2026-11-09|一001", "2026-11-07|六018",
    ]
    same_day = [e for e in walked["entries"] if e["match_uid"] != "2026-11-08|日001"]
    assert any("per_day" in e["reasons"] for e in same_day)


def test_empty_cells_get_the_schedule_reason_not_a_bare_miss():
    item = {
        "match": {"schedule_status": "postponed", "schedule_note": "推迟至 2026-11-09 19:00（北京时间）"},
        "ah": {"pinnacle": {"close": {"line": None, "home_water": None, "away_water": None,
                                      "cell_status": "unavailable", "cell_status_reason": "not_captured"},
                            "open": {"line": -0.5, "home_water": 0.9, "away_water": 0.9,
                                     "cell_status": "ok", "cell_status_reason": None}}},
    }
    ss.stamp_market_blocks("postponed", item["ah"])
    assert item["ah"]["pinnacle"]["close"]["cell_status_reason"] == "schedule_postponed"
    assert item["ah"]["pinnacle"]["open"]["cell_status"] == "ok"
    from app import reason_codes
    reason_codes.annotate(item)
    ss.overlay_schedule_notes(item)
    assert item["ah"]["pinnacle"]["close"]["cell_status_reason_zh"] == "推迟至 2026-11-09 19:00（北京时间）"
