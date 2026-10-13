"""run_queue：共享限速器总等待超限时，状态为非运行、本轮报告照写、本场留在待处理队列。不调用接口。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_queue as rq  # noqa: E402


def test_wait_exceeded_writes_state_and_report(tmp_path, monkeypatch):
    q, logs, reps = tmp_path / "queue", tmp_path / "logs", tmp_path / "reports"
    q.mkdir()
    rows = [{"fixture_id": i, "match_uid": f"u{i}", "priority": 1} for i in (1, 2)]
    (q / "pending.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    (q / "state.json").write_text(json.dumps({"hist_mode": "running"}), encoding="utf-8")
    for name, val in (("QUEUE", q), ("LOG_DIR", logs), ("REPORTS", reps)):
        monkeypatch.setattr(rq, name, val)
    monkeypatch.setattr(rq, "assert_dual_write_off", lambda: None)
    monkeypatch.setenv("FIVEDOLLAR_FOOTBALL_API_KEY", "test-key")

    def boom(client, row, *, dry_run):
        raise rq._yield_mod.SharedYieldWaitExceeded(12.0, "rate_limit", caller="run_queue", max_wait_sec=10)

    monkeypatch.setattr(rq, "pull_fixture", boom)
    monkeypatch.setattr(sys, "argv", ["run_queue.py", "--limit", "5", "--no-yield-check"])
    assert rq.main() == 0
    st = json.loads((q / "state.json").read_text(encoding="utf-8"))
    assert st["hist_mode"] == "stopped" and st["last_stop"] == "yield_wait_exceeded"
    assert st["pending"] == 2
    out = list(reps.glob("run_*.json"))
    assert len(out) == 1
    rep = json.loads(out[0].read_text(encoding="utf-8"))
    assert rep["stop"] == "yield_wait_exceeded" and rep["errors"] == []
    assert rep["paused_events"][0]["wait_reason"] == "rate_limit"
    left = [json.loads(x) for x in (q / "pending.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["fixture_id"] for x in left] == [1, 2]
