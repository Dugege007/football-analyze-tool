"""预期进球六列：映射缺失、完整精度、半场缺一侧、未完赛、估算、下半场三种来源。"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO / "scripts" / "live"))

from app import xg_obs  # noqa: E402
import result_ingest as ingest  # noqa: E402

TZ = timezone(timedelta(hours=8))
AS_OF = "2026-10-16T21:00:00+08:00"


def _stats(ft_home, ft_away, ht_home, ht_away, h2_home="omit", h2_away="omit", *, ft_away_estimated=False, ht_home_estimated=False, h2_home_estimated=False):
    """组装 BSD 统计。h2_* 为 omit 时不给出独立下半场。"""
    def block(value, estimated):
        if value is None:
            return {"xg": {"actual": None, "estimated": None}}
        return {"xg": {"actual": value, "estimated": estimated}}

    stats = {
        "home": block(ft_home, False),
        "away": block(ft_away, ft_away_estimated),
        "first_half": {
            "home": block(ht_home, ht_home_estimated),
            "away": block(ht_away, False),
        },
    }
    if h2_home != "omit" or h2_away != "omit":
        stats["second_half"] = {
            "home": block(None if h2_home == "omit" else h2_home, h2_home_estimated),
            "away": block(None if h2_away == "omit" else h2_away, False),
        }
    return {"event_id": 223510, "xg_estimated": False, "stats": stats, "shotmap": [{"xg": 0.34}]}


def _shells_from_body(body, **kwargs):
    payload = xg_obs.build_payload_from_bsd_stats(body, provider_event_id=223510, as_of=AS_OF)
    observation = {"as_of": AS_OF, "source": "bsd", "payload": payload}
    shells = xg_obs.shells_for_match(
        finished=kwargs.get("finished", True),
        schema_ready=kwargs.get("schema_ready", True),
        mapped=kwargs.get("mapped", True),
        observation=None if kwargs.get("drop_observation") else observation,
    )
    return payload, shells


def test_missing_mapping_is_unavailable_even_if_observation_exists():
    payload, _ = _shells_from_body(_stats(1.573892, 0.8, 0.4, 0.3, 0.9, 0.4))
    shells = xg_obs.shells_for_match(
        finished=True, schema_ready=True, mapped=False,
        observation={"as_of": AS_OF, "source": "bsd", "payload": payload},
    )
    for key in xg_obs.COLUMN_KEYS:
        assert shells[key]["status"] == "unavailable"
        assert shells[key]["reason"] == "no_bsd_mapping"
        assert shells[key]["reason_zh"] == "本场没有对应的 BSD 赛事编号"
        assert shells[key]["value"] is None


def test_bsd_values_keep_full_precision():
    raw = 1.573892
    payload, shells = _shells_from_body(_stats(raw, 0.80421, 0.401, 0.2, 1.172892, 0.60421))
    assert shells["xg_ft_home"]["status"] == "ok"
    assert shells["xg_ft_home"]["value"] == raw
    assert shells["xg_ft_home"]["value"] != round(raw, 2)
    assert payload["ft"]["home"] == raw
    assert "shotmap" not in payload


def test_half_missing_one_side_does_not_blank_the_other():
    payload, shells = _shells_from_body(_stats(1.5, 0.8, None, 0.33, 0.7, 0.47))
    assert shells["xg_ht_home"]["status"] == "unavailable"
    assert shells["xg_ht_home"]["reason"] == "half_side_missing"
    assert shells["xg_ht_home"]["value"] is None
    assert shells["xg_ht_away"]["status"] == "ok"
    assert shells["xg_ht_away"]["value"] == 0.33
    assert shells["xg_ft_home"]["status"] == "ok"
    assert payload["ht"]["home"] is None


def test_unfinished_stays_not_filled():
    payload, _ = _shells_from_body(_stats(1.2, 0.4, 0.5, 0.1, 0.7, 0.3))
    shells = xg_obs.shells_for_match(
        finished=False, schema_ready=True, mapped=True,
        observation={"as_of": AS_OF, "source": "bsd", "payload": payload},
    )
    for key in xg_obs.COLUMN_KEYS:
        assert shells[key]["status"] == "not_filled"
        assert shells[key]["value"] is None
        assert shells[key]["reason"] is None


def test_estimated_flag_is_marked_on_that_column_only():
    _, shells = _shells_from_body(_stats(
        1.25, 0.84219, 0.5, 0.3, 0.75, 0.54219, ft_away_estimated=True,
    ))
    assert shells["xg_ft_away"]["quality"] == "estimated"
    assert shells["xg_ft_away"]["value"] == 0.84219
    assert shells["xg_ft_away"]["status"] == "ok"
    assert shells["xg_ft_home"]["quality"] == "measured"
    assert shells["xg_ft_home"]["quality"] != "estimated"


def test_independent_second_half_is_not_replaced_by_subtraction():
    full_time = 2.0
    first_half = 0.5
    second_half = 1.111
    payload, shells = _shells_from_body(_stats(full_time, 1.0, first_half, 0.25, second_half, 0.75))
    assert payload["h2"]["home"] == second_half
    assert payload["h2"]["home"] != full_time - first_half
    assert payload["h2_origin"]["home"] == "bsd"
    assert shells["xg_2h_home"]["value"] == second_half
    assert shells["xg_2h_home"]["quality"] == "measured"
    assert shells["xg_2h_home"]["reason"] is None


def test_derived_second_half_subtracts_at_full_precision():
    full_time = 1.573892
    first_half = 0.201
    payload, shells = _shells_from_body(_stats(full_time, 0.9, first_half, 0.4))
    expected = full_time - first_half
    assert payload["h2"]["home"] == expected
    assert payload["h2_origin"]["home"] == "derived"
    assert payload["quality_by_side"]["h2"]["home"] == "derived"
    assert shells["xg_2h_home"]["value"] == expected
    assert shells["xg_2h_home"]["value"] != round(expected, 2)
    assert shells["xg_2h_home"]["quality"] == "derived"
    assert shells["xg_2h_home"]["status"] == "ok"
    assert shells["xg_2h_home"]["reason"] == "derived_from_full_minus_half"
    assert shells["xg_2h_home"]["reason_zh"] == "由全场减上半场得出"
    # 接口返回的就是已写入的数，不是展示用的两位小数。
    assert json.loads(json.dumps(payload["h2"]["home"])) == expected


def test_missing_full_or_half_makes_second_half_unavailable_not_zero():
    payload, shells = _shells_from_body(_stats(1.8, 0.4, None, 0.1))
    assert payload["h2"]["home"] is None
    assert shells["xg_2h_home"]["status"] == "unavailable"
    assert shells["xg_2h_home"]["value"] is None
    assert shells["xg_2h_home"]["value"] != 0
    assert shells["xg_2h_home"]["reason"] == "second_half_inputs_missing"
    assert shells["xg_2h_home"]["reason_zh"] == "全场或上半场这一侧没有数值，不能得出下半场预期进球"
    # 客队上半场和全场都在，下半场仍由减法得到，不跟主队绑成同一个状态。
    assert shells["xg_2h_away"]["status"] == "ok"
    assert shells["xg_2h_away"]["value"] == pytest.approx(0.4 - 0.1)
    assert shells["xg_2h_away"]["quality"] == "derived"

    no_full, no_full_shells = _shells_from_body(_stats(None, 0.4, 0.2, 0.1))
    assert no_full["h2"]["home"] is None
    assert no_full_shells["xg_2h_home"]["value"] is None
    assert no_full_shells["xg_2h_home"]["status"] == "unavailable"


def test_no_coverage_is_unavailable_and_flat_names_match():
    payload, shells = _shells_from_body({"stats": {}, "shotmap": [{"xg": 1}]})
    assert payload["quality"] == "unavailable"
    assert payload["reason"] == "no_bsd_coverage"
    assert shells["xg_ft_home"]["status"] == "unavailable"
    assert shells["xg_ft_home"]["reason"] == "no_bsd_coverage"
    flat = xg_obs.flat_columns(shells)
    assert set(xg_obs.COLUMN_KEYS).issubset(flat)
    assert flat["xg_ft_home"] is None
    assert flat["xg_ft_home_status"] == "unavailable"
    assert flat["xg_2h_away_reason"] == "no_bsd_coverage"


def test_upsert_stores_derived_value_without_rounding(tmp_path):
    conn = sqlite3.connect(tmp_path / "app.db")
    full_time = 1.573892
    first_half = 0.201
    payload = xg_obs.build_payload_from_bsd_stats(
        _stats(full_time, 0.5, first_half, 0.2), provider_event_id=99, as_of=AS_OF,
    )
    xg_obs.upsert_xg_observation(conn, 7, payload, as_of=AS_OF, fetched_at=AS_OF)
    conn.commit()
    raw = conn.execute("SELECT payload_json, quality FROM stats_obs WHERE match_id = 7 AND kind = 'xg'").fetchone()
    stored = json.loads(raw[0])
    assert stored["h2"]["home"] == full_time - first_half
    assert stored["h2"]["home"] != round(full_time - first_half, 2)
    assert "shotmap" not in stored
    conn.close()


def test_queue_does_not_call_bsd_without_mapping(tmp_path):
    conn = sqlite3.connect(tmp_path / "app.db")
    now = datetime(2026, 10, 16, 21, 0, tzinfo=TZ)
    xg_obs.enqueue_finished_matches(conn, [3], now)

    class Boom:
        def fetch_event_stats(self, event_id):
            raise AssertionError("没有映射时不应该请求 BSD")

    report = xg_obs.pull_due_expected_goals(
        conn, now, client=Boom(), quota_path=tmp_path / "quota.json", sleep_seconds=0,
    )
    assert report["fetched"] == 0
    assert report["no_bsd_mapping"] == 1
    conn.close()


def test_queue_schedules_second_pull_three_hours_later(tmp_path):
    conn = sqlite3.connect(tmp_path / "app.db")
    conn.execute("CREATE TABLE bsd_event_map (match_id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL)")
    conn.execute("INSERT INTO bsd_event_map VALUES (5, 88001)")
    now = datetime(2026, 10, 16, 18, 0, tzinfo=TZ)
    xg_obs.enqueue_finished_matches(conn, [5], now)

    class Fake:
        def fetch_event_stats(self, event_id):
            assert event_id == 88001
            return {"http_status": 200, "body": _stats(1.573892, 0.4, 0.2, 0.1), "error": None}

    report = xg_obs.pull_due_expected_goals(
        conn, now, client=Fake(), quota_path=tmp_path / "quota.json", sleep_seconds=0, daily_cap=7500,
    )
    assert report["stored"] == 1
    row = conn.execute("SELECT attempts, done_at, due_at FROM xg_fetch_queue WHERE match_id = 5").fetchone()
    assert row[0] == 1 and row[1] is None
    assert row[2] == xg_obs.iso_cn(now + timedelta(hours=3))
    stored = json.loads(conn.execute("SELECT payload_json FROM stats_obs WHERE match_id = 5").fetchone()[0])
    assert stored["h2"]["home"] == pytest.approx(1.573892 - 0.2)
    assert stored["h2_origin"]["home"] == "derived"

    later = now + timedelta(hours=3)
    again = xg_obs.pull_due_expected_goals(
        conn, later, client=Fake(), quota_path=tmp_path / "quota.json", sleep_seconds=0,
    )
    assert again["stored"] == 1
    done = conn.execute("SELECT attempts, done_at FROM xg_fetch_queue WHERE match_id = 5").fetchone()
    assert done[0] == 2 and done[1] is not None
    conn.close()


def test_daily_cap_stops_at_limit(tmp_path):
    path = tmp_path / "quota.json"
    now = datetime(2026, 10, 16, 12, 0, tzinfo=TZ)
    assert xg_obs.reserve_daily_call(path, now, cap=2) is True
    assert xg_obs.reserve_daily_call(path, now, cap=2) is True
    assert xg_obs.reserve_daily_call(path, now, cap=2) is False
    # 下一天的额度重新计算，不沿用上一天的计数。
    tomorrow = now + timedelta(days=1)
    assert xg_obs.reserve_daily_call(path, tomorrow, cap=2) is True


def test_result_ingest_enqueues_without_network(tmp_path, monkeypatch):
    folder = tmp_path / "v2d3"
    folder.mkdir()
    path = str(folder / "app.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE import_batches (
          id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT NOT NULL, month TEXT,
          imported_at TEXT NOT NULL DEFAULT (datetime('now')), row_count INTEGER, status TEXT NOT NULL DEFAULT 'ok'
        );
        CREATE TABLE matches (
          id INTEGER PRIMARY KEY, match_uid TEXT, scope TEXT, jingcai_date TEXT, jc_id TEXT,
          home_team TEXT, away_team TEXT, kickoff_at TEXT
        );
        CREATE TABLE results (
          match_id INTEGER PRIMARY KEY, home_goals INTEGER, away_goals INTEGER, total_goals INTEGER, wdl TEXT
        );
        CREATE TABLE odds_asian (
          id INTEGER PRIMARY KEY AUTOINCREMENT, match_id INTEGER, book TEXT, phase TEXT,
          handicap REAL, home_water REAL, away_water REAL
        );
        INSERT INTO matches VALUES (1, 'a', 'jingcai', '2026-10-10', '六001', '主队', '客队', '2026-10-10T13:00:00+08:00');
        INSERT INTO odds_asian (match_id, book, phase, handicap, home_water, away_water)
        VALUES (1, 'macau', 'close', 0.25, 0.90, 0.95);
        """
    )
    conn.commit()
    conn.close()

    def explode(*_args, **_kwargs):
        raise AssertionError("赛果入库入队时不应该创建 BSD 客户端")

    monkeypatch.setattr(xg_obs, "BsdStatsClient", explode)
    monkeypatch.delenv("BSD_BZZOIRO_API_KEY", raising=False)
    now = datetime(2026, 10, 16, 18, 0, tzinfo=TZ)
    result = ingest.apply(
        path,
        [({"id": 1}, {"action": "write", "score": (2, 1), "source": "5df_csl", "flags": []})],
        tmp_path / "backup",
        "test",
        now,
    )
    assert result["inserted"] == 1
    assert result["xg_queue"]["enqueued"] == 1
    check = sqlite3.connect(path)
    queued = check.execute("SELECT due_at, attempts, done_at FROM xg_fetch_queue WHERE match_id = 1").fetchone()
    assert queued[1] == 0 and queued[2] is None
    assert check.execute("SELECT COUNT(*) FROM stats_obs").fetchone()[0] == 0
    check.close()


def test_backfill_dry_run_report_counts_reasons(tmp_path):
    conn = sqlite3.connect(tmp_path / "app.db")
    conn.executescript(
        """
        CREATE TABLE matches (id INTEGER PRIMARY KEY);
        CREATE TABLE results (match_id INTEGER PRIMARY KEY, home_goals INTEGER, away_goals INTEGER);
        INSERT INTO matches VALUES (1), (2), (3);
        INSERT INTO results VALUES (1, 1, 0), (2, 0, 0), (3, 2, 2);
        """
    )
    report = xg_obs.classify_backfill(conn)
    assert report["finished_matches"] == 3
    assert report["schema_ready"] is False
    assert report["unavailable_reasons"]["not_collected"] == 3
    xg_obs.ensure_xg_schema(conn)
    conn.execute("INSERT INTO bsd_event_map (match_id, event_id) VALUES (1, 10)")
    payload = xg_obs.build_payload_from_bsd_stats(_stats(1.2, 0.2, 0.4, 0.1), provider_event_id=10, as_of=AS_OF)
    xg_obs.upsert_xg_observation(conn, 1, payload, as_of=AS_OF, fetched_at=AS_OF)
    conn.commit()
    again = xg_obs.classify_backfill(conn)
    assert again["unavailable_reasons"]["no_bsd_mapping"] == 2
    assert again["unavailable_reasons"]["stored"] == 1
    assert again["would_fetch"] == 0
    conn.close()
