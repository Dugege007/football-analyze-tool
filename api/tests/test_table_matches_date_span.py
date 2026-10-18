"""GET /table/matches 竞彩日跨度最多 31 个日历日。"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.db as adb  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_SCHEMA = """
CREATE TABLE matches (
  id INTEGER PRIMARY KEY,
  match_uid TEXT, scope TEXT, jingcai_date TEXT, weekday TEXT,
  kickoff_hour INTEGER, jc_id TEXT, jc_no INTEGER,
  competition_name TEXT, competition_type TEXT,
  home_team TEXT, away_team TEXT,
  kickoff_at TEXT, kickoff_minute_known INTEGER,
  league_id INTEGER, home_team_id INTEGER, away_team_id INTEGER
);
CREATE TABLE leagues (id INTEGER PRIMARY KEY, name_zh_canonical TEXT);
CREATE TABLE teams (id INTEGER PRIMARY KEY, name_zh_canonical TEXT);
CREATE TABLE match_meta (match_id INTEGER, extras_json TEXT);
CREATE TABLE predictions (match_id INTEGER, strategy TEXT, direction TEXT);
CREATE TABLE results (match_id INTEGER, home_goals INTEGER, away_goals INTEGER, total_goals INTEGER, wdl TEXT);
CREATE TABLE odds_asian (
  match_id INTEGER, book TEXT, phase TEXT, handicap REAL,
  home_water REAL, away_water REAL, water_src TEXT,
  water_censored INTEGER, extras_json TEXT
);
CREATE TABLE odds_euro_home (match_id INTEGER, book TEXT, phase TEXT, home_win REAL);
CREATE TABLE odds_jc_home (match_id INTEGER, phase TEXT, home_win REAL);
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    conn.close()
    monkeypatch.setattr(adb, "DB_PATH", db)
    return TestClient(app)


def test_span_31_days_ok(client):
    r = client.get("/table/matches", params={"date_from": "2026-01-01", "date_to": "2026-02-01"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["date_from"] == "2026-01-01"
    assert body["date_to"] == "2026-02-01"
    assert (date.fromisoformat(body["date_to"]) - date.fromisoformat(body["date_from"])).days == 31


def test_span_32_days_400(client):
    r = client.get("/table/matches", params={"date_from": "2026-01-01", "date_to": "2026-02-02"})
    assert r.status_code == 400
    assert r.json()["detail"] == "竞彩日跨度不能超过 31 天"


def test_date_from_after_date_to_still_400(client):
    r = client.get("/table/matches", params={"date_from": "2026-02-02", "date_to": "2026-01-01"})
    assert r.status_code == 400
    assert r.json()["detail"] == "date_from > date_to"


def test_default_seven_day_window_ok(client):
    r = client.get("/table/matches")
    assert r.status_code == 200, r.text
    body = r.json()
    span = (date.fromisoformat(body["date_to"]) - date.fromisoformat(body["date_from"])).days
    assert span == 6
