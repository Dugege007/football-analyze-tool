"""result.status：退款、推迟、取消、腰斩（来自 result_ingest_flags）在嵌套和扁平两种格式里的输出。
全部在临时副本上跑；result_ingest_flags 只在临时副本里建，正式库结构不变。"""
from __future__ import annotations

import sqlite3

import pytest

from test_table_matches import _copy_prod, _get, _insert_match, _row, adb, app, TestClient, tm  # noqa: F401

D = "2001-03-04"
FLAGS_SQL = ("CREATE TABLE IF NOT EXISTS result_ingest_flags (match_id INTEGER NOT NULL, flag TEXT NOT NULL, "
             "detail_json TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, resolved_at TEXT, "
             "PRIMARY KEY (match_id, flag))")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "app.db"
    _copy_prod(p)
    monkeypatch.setattr(adb, "DB_PATH", p)
    return p


@pytest.fixture()
def client():
    return TestClient(app)


def _setup(db, *, flags: bool):
    c = sqlite3.connect(str(db))
    ids = {}
    for code, goals in (("三001", None), ("三002", (2, 1)), ("三003", None), ("三004", None)):
        ids[code] = _insert_match(c, f"{D}|{code}", scope="jingcai", date=D, hour=12,
                                  kickoff_at=f"{D}T12:00:00+08:00", jc_id=code, goals=goals)
    if flags:
        c.execute(FLAGS_SQL)
        c.execute("INSERT INTO result_ingest_flags VALUES (?, 'refund', '{}', 'x', 'x', NULL)", (ids["三001"],))
        c.execute("INSERT INTO result_ingest_flags VALUES (?, 'postponed', '{}', 'x', 'x', NULL)", (ids["三003"],))
        c.execute("INSERT INTO result_ingest_flags VALUES (?, 'refund', '{}', 'x', 'x', 'y')", (ids["三004"],))
    c.commit()
    c.close()


def _q(client, **kw):
    return _get(client, date_from=D, date_to=D, **kw)


def test_nested_refund_postponed_finished(db, client):
    _setup(db, flags=True)
    d = _q(client)
    r1 = _row(d, f"{D}|三001")
    assert r1["result"]["status"] == "refund" and r1["result"]["score"] is None and r1["result"]["home_goals"] is None
    assert r1["result_hidden_reason"] == "refund" and r1["settlement"] is None
    assert _row(d, f"{D}|三003")["result"]["status"] == "postponed"
    r2 = _row(d, f"{D}|三002")
    assert r2["result"]["status"] == "finished" and r2["result"]["score"] == "2-1"
    r4 = _row(d, f"{D}|三004")  # 已解决的标记不生效
    assert r4["result"] is None and r4["result_hidden_reason"] == "no_result"


def test_flat_result_status_column(db, client):
    _setup(db, flags=True)
    d = _q(client, format="flat")
    r1 = _row(d, f"{D}|三001")
    assert r1["result_status"] == "refund" and r1["result_score"] is None
    assert _row(d, f"{D}|三002")["result_status"] == "finished"
    assert "result_status" in _row(d, f"{D}|三004") and _row(d, f"{D}|三004")["result_status"] is None


def test_without_flags_table_no_status(db, client):
    _setup(db, flags=False)
    d = _q(client)
    assert _row(d, f"{D}|三001")["result"] is None
    assert _row(d, f"{D}|三001")["result_hidden_reason"] == "no_result"
    assert tm.load_result_special_status(sqlite3.connect(str(db))) == {}
