"""2026-10-13：共享限速器总等待上限、共享额度暂停、睡前日志、作废预约清理。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import shared_api_yield as Y  # noqa: E402

CN = timezone(timedelta(hours=8))
START = datetime(2026, 10, 9, 2, 0, tzinfo=CN)


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start.timestamp()
        self.sleeps: list[float] = []

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def clock(self):
        return self.t

    def now(self):
        return datetime.fromtimestamp(self.t, CN)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(Y, "LOG", tmp_path / "log.jsonl")
    fc = FakeClock(START)

    def mk(name="t", **kw):
        return Y.Gate(name, sleep=fc.sleep, clock=fc.clock, now=fc.now, ledger=tmp_path / "l.json", **kw)
    return fc, mk, tmp_path


def _log_lines(tmp_path):
    return [json.loads(x) for x in (tmp_path / "log.jsonl").read_text().splitlines()]


def test_exception_exported_and_default_cap():
    assert issubclass(Y.SharedYieldWaitExceeded, RuntimeError)
    e = Y.SharedYieldWaitExceeded(12.5, "quota_pause", caller="x", max_wait_sec=10)
    assert (e.waited_sec, e.reason, e.caller, e.max_wait_sec) == (12.5, "quota_pause", "x", 10.0)
    assert Y.DEFAULT_MAX_WAIT_SEC == 300.0


def test_wait_cap_raises_on_long_quota_pause(env):
    fc, mk, _ = env
    g = mk()
    g.after_response({"X-RateLimit-Remaining": "3", "X-RateLimit-Reset": "600"})
    t0 = fc.clock()
    with pytest.raises(Y.SharedYieldWaitExceeded) as ei:
        g.before_request(max_wait_sec=300)
    assert ei.value.reason == "quota_pause" and ei.value.waited_sec == 0
    assert fc.clock() == t0  # 不先睡再报错
    g2 = mk(max_wait_sec=1000)
    g2.before_request()
    assert fc.clock() - t0 >= 600


def test_wait_cap_raises_on_rate_wait(env):
    fc, mk, tmp = env
    g = mk()
    for _ in range(16):
        g.before_request()
    with pytest.raises(Y.SharedYieldWaitExceeded) as ei:
        g.before_request(max_wait_sec=1)
    assert ei.value.reason == "rate_limit"
    # 报错时不留下预约
    stamps = json.loads((tmp / "l.json").read_text())
    assert len(stamps) == 16


def test_yield_window_not_counted_in_cap(env):
    fc, mk, _ = env
    fc.t = datetime(2026, 10, 9, 14, 56, tzinfo=CN).timestamp()
    g = mk(max_wait_sec=5)
    g.before_request()
    assert not Y.must_yield(fc.now())


@pytest.mark.parametrize("kind", ["epoch_s", "epoch_ms", "remaining"])
def test_reset_formats(env, kind):
    fc, mk, _ = env
    now = fc.clock()
    rst = {"epoch_s": now + 40, "epoch_ms": (now + 40) * 1000, "remaining": 40}[kind]
    assert Y.parse_reset_seconds(rst, now) == pytest.approx(40)
    g = mk()
    g.after_response({"X-RateLimit-Remaining": "10", "X-RateLimit-Reset": str(rst)})
    assert g.pause_until == pytest.approx(now + 41)


def test_pause_shared_across_processes(env):
    fc, mk, tmp = env
    a, b = mk("a"), mk("b")
    a.after_response({"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": "30"})
    assert Y.pause_path(tmp / "l.json").exists()
    assert b.shared_pause_until() == pytest.approx(fc.clock() + 31)
    t0 = fc.clock()
    b.before_request()
    assert fc.clock() - t0 >= 31


def test_log_before_every_sleep(env):
    fc, mk, tmp = env
    g = mk()
    g.after_response({"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": "10"})
    for _ in range(3):
        g.before_request()
    sl = [x for x in _log_lines(tmp) if x["event"] == "sleep"]
    assert len(sl) == len(fc.sleeps) and len(sl) >= 3
    assert {x["reason"] for x in sl} >= {"quota_pause", "rate_limit"}
    assert all(x["sleep_s"] > 0 for x in sl)


def test_reserve_at_most_one_interval_ahead(env):
    fc, mk, tmp = env
    g = mk()
    for _ in range(20):
        g.before_request()
        stamps = json.loads((tmp / "l.json").read_text())
        assert max(stamps) <= fc.clock() + Y.MIN_INTERVAL_SEC + 1e-6


def test_stale_stamp_removed_on_rereserve(env):
    fc, mk, tmp = env
    a = mk("a")
    a.before_request()
    # a 刚预约一个未来时刻，然后放弃：删掉后账本只剩真正用过的
    w = a._reserve_slot()
    assert w > 0 and a._last_stamp is not None
    stale = a._last_stamp
    a._drop_stamp(stale)
    stamps = json.loads((tmp / "l.json").read_text())
    assert stale not in stamps and len(stamps) == 1


def test_rereserve_after_shared_pause_drops_stale(env):
    fc, mk, tmp = env
    a, b = mk("a"), mk("b")
    a.before_request()
    orig = b._nap

    def nap(reason, s):  # b 等速率期间，a 设了共享暂停
        orig(reason, s)
        if reason == "rate_limit" and not b.shared_pause_until():
            a.after_response({"X-RateLimit-Remaining": "1", "X-RateLimit-Reset": "20"})
    b._nap = nap
    b.before_request()
    stamps = json.loads((tmp / "l.json").read_text())
    assert len(stamps) == 2  # a 一个、b 最终一个；作废的那个已删除
    assert b.stats["requests"] == 1


def test_constants_unchanged():
    assert Y.MIN_INTERVAL_SEC == 3.75 and Y.ANALYST_LIVE_MAX_PER_MIN == 24
    assert Y.YIELD_WINDOWS == (((11, 5), (11, 20)), ((14, 55), (15, 15)), ((21, 55), (22, 15)))
