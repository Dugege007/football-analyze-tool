"""GET /matches/dates：日历置灰用的竞彩日列表（只认 matches 行是否存在）。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.db as adb  # noqa: E402
from app.main import app  # noqa: E402


def _build_db(path: Path) -> None:
    c = sqlite3.connect(str(path))
    # 只建 matches。接口若去连赔率 / 预测 / 赛果，这里会直接失败。
    c.executescript(
        """
        CREATE TABLE matches (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_uid TEXT NOT NULL UNIQUE,
          scope TEXT NOT NULL,
          jingcai_date TEXT NOT NULL
        );
        """
    )
    # 故意打乱插入顺序，并让同一天有多行，确认 DISTINCT + 升序不是插入顺序的副作用。
    rows = [
        ("2026-06-05|a", "jingcai", "2026-06-05"),
        ("2026-06-01|out", "jingcai", "2026-06-01"),
        ("2026-06-04|b", "jingcai", "2026-06-04"),
        ("2026-06-04|c", "jingcai", "2026-06-04"),
        ("2026-06-02|in", "jingcai", "2026-06-02"),
        ("2026-06-03|ext", "extra", "2026-06-03"),
        ("2026-06-06|out-ext", "extra", "2026-06-06"),
        ("2026-06-04|ext", "extra", "2026-06-04"),
    ]
    c.executemany(
        "INSERT INTO matches (match_uid, scope, jingcai_date) VALUES (?,?,?)",
        rows,
    )
    c.commit()
    c.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "dates.db"
    _build_db(db)
    monkeypatch.setattr(adb, "DB_PATH", db)
    monkeypatch.setattr(adb, "READONLY", False)
    monkeypatch.setattr(adb, "DB_LABEL", "live")
    return TestClient(app)


def test_closed_interval_distinct_sorted(client):
    r = client.get("/matches/dates", params={"from": "2026-06-02", "to": "2026-06-05"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"dates": ["2026-06-02", "2026-06-04", "2026-06-05"]}
    assert "2026-06-01" not in body["dates"]
    assert "2026-06-03" not in body["dates"]
    assert "2026-06-06" not in body["dates"]


def test_single_day_interval_is_inclusive(client):
    r = client.get("/matches/dates", params={"from": "2026-06-02", "to": "2026-06-02"})
    assert r.status_code == 200
    assert r.json() == {"dates": ["2026-06-02"]}


def test_scope_filter_and_default_jingcai(client):
    jingcai = client.get(
        "/matches/dates", params={"from": "2026-06-01", "to": "2026-06-06", "scope": "jingcai"}
    )
    default = client.get("/matches/dates", params={"from": "2026-06-01", "to": "2026-06-06"})
    extra = client.get(
        "/matches/dates", params={"from": "2026-06-01", "to": "2026-06-06", "scope": "extra"}
    )
    all_scope = client.get(
        "/matches/dates", params={"from": "2026-06-01", "to": "2026-06-06", "scope": "all"}
    )
    assert jingcai.status_code == extra.status_code == all_scope.status_code == 200
    assert jingcai.json()["dates"] == ["2026-06-01", "2026-06-02", "2026-06-04", "2026-06-05"]
    assert default.json() == jingcai.json()
    assert extra.json()["dates"] == ["2026-06-03", "2026-06-04", "2026-06-06"]
    assert all_scope.json()["dates"] == [
        "2026-06-01",
        "2026-06-02",
        "2026-06-03",
        "2026-06-04",
        "2026-06-05",
        "2026-06-06",
    ]


def test_extended_is_alias_of_extra(client):
    extra = client.get(
        "/matches/dates", params={"from": "2026-06-01", "to": "2026-06-06", "scope": "extra"}
    )
    extended = client.get(
        "/matches/dates", params={"from": "2026-06-01", "to": "2026-06-06", "scope": "extended"}
    )
    assert extended.status_code == 200
    assert extended.json() == extra.json()
    assert "2026-06-02" not in extended.json()["dates"]


def test_empty_interval(client):
    r = client.get("/matches/dates", params={"from": "2026-07-01", "to": "2026-07-03", "scope": "all"})
    assert r.status_code == 200
    assert r.json() == {"dates": []}


def test_missing_params(client):
    no_from = client.get("/matches/dates", params={"to": "2026-06-05"})
    no_to = client.get("/matches/dates", params={"from": "2026-06-02"})
    neither = client.get("/matches/dates")
    assert no_from.status_code == 422
    assert no_to.status_code == 422
    assert neither.status_code == 422


def test_from_after_to(client):
    r = client.get("/matches/dates", params={"from": "2026-06-05", "to": "2026-06-02"})
    assert r.status_code == 400
    assert r.json()["detail"] == "from > to"


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-13-01", "to": "2026-13-02"},
        {"from": "2026-02-31", "to": "2026-03-01"},
        {"from": "2026/06/02", "to": "2026-06-05"},
        {"from": "2026-06-02", "to": "06-05"},
        {"from": "", "to": "2026-06-05"},
    ],
)
def test_invalid_dates(client, params):
    r = client.get("/matches/dates", params=params)
    assert r.status_code == 400
    assert "YYYY-MM-DD" in r.json()["detail"]


def test_dates_path_is_not_captured_as_match_id(client):
    dates = client.get("/matches/dates", params={"from": "2026-06-02", "to": "2026-06-02"})
    assert dates.status_code == 200
    assert list(dates.json()) == ["dates"]

    # 同库上详情路由仍在；「dates」若被 {match_id} 吞掉，会落到 match not found。
    missing = client.get("/matches/not-a-real-uid")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "match not found"
    assert dates.json().get("detail") != "match not found"
