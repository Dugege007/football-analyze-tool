"""map_csl_fixtures：共享限速器总等待超限时本轮正常收尾（不崩溃、不写错误文件、照常写运行报告）。不调用接口。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import map_csl_fixtures as mc  # noqa: E402


def _isolate(monkeypatch, tmp_path):
    q = tmp_path / "queue"
    q.mkdir()
    (q / "pending.jsonl").write_text("", encoding="utf-8")
    rows = [{"match_uid": "u1", "jingcai_date": "2026-10-12", "jc_id": "周一001",
             "home_team": "甲", "away_team": "乙"}]
    (q / "unmapped.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                      encoding="utf-8")
    for name, val in (("QUEUE", q), ("RAW_CSL", tmp_path / "raw" / "csl"), ("LOG_DIR", tmp_path / "logs"),
                      ("MAP_PATH", tmp_path / "csl_fixture_map.json"),
                      ("FIXTURE_MAP", tmp_path / "fixture_map.json"), ("EXTRA_CSL_DIRS", [])):
        monkeypatch.setattr(mc, name, val)
    monkeypatch.setenv("FIVEDOLLAR_FOOTBALL_API_KEY", "test-key")

    def boom(req, timeout=60):
        raise mc._yield_mod.SharedYieldWaitExceeded(0.0, "quota_pause", caller="map_csl_fixtures",
                                                    max_wait_sec=300)
    monkeypatch.setattr(mc, "_gated_urlopen", boom)
    monkeypatch.setattr(mc, "GAP_SEC", 0.0)
    return q


def test_load_or_fetch_day_returns_yield_wait_exceeded(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    client = mc.RateClient("test-key", gap=0.0)
    data, fetched, src = mc.load_or_fetch_day(client, "2026-10-12", force=True)
    assert (data, fetched, src) == (None, False, "yield_wait_exceeded")
    assert client.stopped_quota is True and client.yield_wait_exceeded == "quota_pause"
    assert not (tmp_path / "raw" / "csl" / "2026-10-12.err.json").exists()
    assert "yield_wait_exceeded" in (tmp_path / "logs" / "csl_map_call_log.tsv").read_text(encoding="utf-8")


def test_main_finishes_round_and_writes_report(tmp_path, monkeypatch):
    q = _isolate(monkeypatch, tmp_path)
    before = (q / "unmapped.jsonl").read_text(encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["map_csl_fixtures.py", "--days", "1", "--skip-rebuild", "--force"])
    assert mc.main() == 0
    rep = json.loads((tmp_path / "logs" / "csl_map_last_run.json").read_text(encoding="utf-8"))
    assert rep["stopped_reason"] == "yield_wait_exceeded"
    assert rep["days_failed"] == [{"date": "2026-10-12", "src": "yield_wait_exceeded"}]
    assert (q / "unmapped.jsonl").read_text(encoding="utf-8") == before
    assert not list((tmp_path / "raw").rglob("*.err.json")) if (tmp_path / "raw").exists() else True
