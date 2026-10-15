"""Follow-up fixes after the research database was merged into the formal database (2026-10-15).

1. is_5df_macau_row also recognises the extras_json key "src" (for example "5df_hist:macauslot").
2. Import batch names are mapped to agreed source codes and never printed as a source.
3. The manual Macau opening cell takes its earliest quote time only from manual rows, never from 5DF rows.
The tests read the formal database only through the sandbox copy and read-only connections.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import table_matches as tm  # noqa: E402

AGREED_SOURCE_CODES = {"5df", "5df_live", "5df_live_opening", "5df_hist_asof_full", "5df_hist_asof",
                       "5df_macauslot_history", "5dollar_history", "5df_odds_snap", "legacy_import",
                       "legacy_home_only", "avg"}


def _row(**kw):
    return kw


@pytest.mark.parametrize("src", ["5df_hist:macauslot", "5df_hist:pinnacle", "5dollar_history_x"])
def test_src_key_in_extras_marks_a_5df_row(src):
    row = _row(source=None, extras_json=json.dumps({"src": src}), water_src="actual")
    assert tm.is_5df_macau_row(row) is True


def test_manual_row_without_markers_is_not_5df():
    assert tm.is_5df_macau_row(_row(source="legacy_import", extras_json=None, water_src=None)) is False
    assert tm.is_5df_macau_row(_row(source=None, extras_json=json.dumps({"src": "manual_entry"}))) is False
    assert tm.is_5df_macau_row(None) is False


@pytest.mark.parametrize("batch,expected", [
    ("5df_hist_gapfill_20261014", "5df_hist_asof_full"),
    ("5df_hist_gapfill_20270101", "5df_hist_asof_full"),  # a future batch follows the same rule
    ("5df_hist_asof", "5df_hist_asof"),
    ("5df_hist_asof_full", "5df_hist_asof_full"),
    ("5df_live", "5df_live"),
    ("legacy_import", "legacy_import"),
    (None, None),
])
def test_batch_name_maps_to_agreed_source_code(batch, expected):
    assert tm.canonical_source_code(batch) == expected
    if expected is not None:
        assert expected in AGREED_SOURCE_CODES


def test_normalized_cell_never_prints_batch_name():
    cell = {"source": "5df_hist_gapfill_20261014", "available": True}
    tm._normalize_one(cell)
    assert cell["source"] == "5df_hist_asof_full"
    assert cell["source_path"] is None
    cell = {"source": "odds_snapshot/rule/open", "_source_code": "5df_hist_gapfill_20261014"}
    tm._normalize_one(cell)
    assert cell["source"] == "5df_hist_asof_full"
    assert "gapfill" not in json.dumps(cell)


class _FakeData:
    def __init__(self, snaps):
        self.timeline = {}
        self.snap_by_match = {1: snaps}


def _snap_rows(specs):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("create table s (book, market, channel, point, source, recorded_at, extras_json)")
    for sp in specs:
        conn.execute("insert into s values (?,?,?,?,?,?,?)", sp)
    return list(conn.execute("select * from s"))


def test_manual_open_earliest_quote_ignores_5df_rows():
    tz = timezone(timedelta(hours=8))
    as_of = datetime(2026, 6, 6, 12, 0, tzinfo=tz)
    rows = _snap_rows([
        ("macau", "asian", "rule", "open", "5df_macauslot_history", "2026-06-01T21:34:45+08:00", "{}"),
        ("macau", "asian", "rule", "open", "legacy_quote", "2026-06-03T10:00:00+08:00", "{}"),
        ("macau", "asian", "rule", "open", None, "2026-06-02T09:00:00+08:00", json.dumps({"src": "5df_hist:macauslot"})),
    ])
    d = _FakeData(rows)
    _, earliest_all = tm._ts_quotes(d, 1, "macau", "asian", as_of)
    assert earliest_all == datetime(2026, 6, 1, 21, 34, 45, tzinfo=tz)
    first, earliest_manual = tm._ts_quotes(d, 1, "macau", "asian", as_of,
                                           row_filter=lambda r: not tm.is_5df_macau_row(r))
    assert earliest_manual == datetime(2026, 6, 3, 10, 0, tzinfo=tz)
    only_5df = _FakeData(rows[:1] + rows[2:])
    _, none_left = tm._ts_quotes(only_5df, 1, "macau", "asian", as_of,
                                 row_filter=lambda r: not tm.is_5df_macau_row(r))
    assert none_left is None
