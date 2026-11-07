"""阶段完成标记单元测试（不依赖外网与正式库）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from stage_marks.mark import (  # noqa: E402
    age_minutes,
    is_fresh,
    read_stage_mark,
    write_stage_mark,
)
from stage_marks import check as check_mod  # noqa: E402


def test_write_and_read_roundtrip(tmp_path: Path):
    path = write_stage_mark(
        "live.tick",
        status="ok",
        summary={"groups": 2, "due": 3},
        source="test",
        root=tmp_path,
        finished_at="2026-11-07T12:00:00+08:00",
    )
    assert path.is_file()
    mark = read_stage_mark("live.tick", root=tmp_path)
    assert mark is not None
    assert mark["schema"] == "stage_mark/v1"
    assert mark["stage"] == "live.tick"
    assert mark["status"] == "ok"
    assert mark["summary"]["groups"] == 2
    assert mark["tz"] == "Asia/Shanghai"
    log = (tmp_path / "log" / "live.tick.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 1


def test_refuse_success_status_typo(tmp_path: Path):
    with pytest.raises(ValueError):
        write_stage_mark("live.tick", status="success", root=tmp_path)


def test_freshness(tmp_path: Path):
    write_stage_mark(
        "live.tick",
        status="ok",
        finished_at="2026-11-07T17:20:00+08:00",
        root=tmp_path,
    )
    mark = read_stage_mark("live.tick", root=tmp_path)
    # 人为构造：用 mark 自身 finished_at 与一个“稍后”的 now
    from datetime import datetime
    from stage_marks.mark import TZ_CN

    now = datetime(2026, 11, 7, 17, 30, tzinfo=TZ_CN)
    assert age_minutes(mark, now=now) == pytest.approx(10.0)
    assert is_fresh(mark, fresh_minutes=20, now=now) is True
    assert is_fresh(mark, fresh_minutes=5, now=now) is False
    fail = dict(mark, status="fail")
    assert is_fresh(fail, fresh_minutes=20, now=now) is False


def test_check_evaluate_live_uses_marks(tmp_path: Path, monkeypatch):
    write_stage_mark(
        "live.tick",
        status="ok",
        finished_at=check_mod.iso_cn(),
        summary={"groups": 0},
        source="test",
        root=tmp_path,
    )
    monkeypatch.setenv("STAGE_MARKS_DIR", str(tmp_path))
    # 伪造新鲜心跳
    hb_path = tmp_path / "heartbeat.json"
    hb_path.write_text(
        json.dumps({"ts": check_mod.iso_cn(), "phase": "tick_done", "pid": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(check_mod, "HEARTBEAT_DEFAULT", hb_path)
    report = check_mod.evaluate("live", use_legacy=False)
    assert report["ok"] is True
    tick = next(s for s in report["stages"] if s["stage"] == "live.tick")
    assert tick["fresh"] is True
