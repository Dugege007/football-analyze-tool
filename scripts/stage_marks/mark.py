"""统一「阶段完成标记」：取数主路径成功后写入，巡检只读标记与心跳。

格式见 docs/ops/stage-completion-marks.md。落盘目录默认
/workspace/odds-data/stage-marks/（可用环境变量 STAGE_MARKS_DIR 覆盖）。

硬规则：只在阶段真正成功（或明确失败收尾）之后调用 write_stage_mark；
禁止在阶段开始前写入 status=ok。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = "stage_mark/v1"
TZ_CN = timezone(timedelta(hours=8))
TZ_NAME = "Asia/Shanghai"

# 阶段名 → 默认新鲜窗口（分钟）。巡检可用 check.py 覆盖。
STAGE_DEFS: dict[str, dict[str, Any]] = {
    "live.tick": {
        "label": "实时采集一轮 tick",
        "fresh_minutes": 20,
        "required_for_profiles": ["live", "all"],
    },
    "live.open_batch": {
        "label": "实时采集开盘／11:10 批次",
        "fresh_minutes": 36 * 60,
        "required_for_profiles": [],
    },
    "live.mid_batch": {
        "label": "实时采集中盘批次",
        "fresh_minutes": 36 * 60,
        "required_for_profiles": [],
    },
    "live.close_batch": {
        "label": "实时采集临盘批次",
        "fresh_minutes": 36 * 60,
        "required_for_profiles": [],
    },
    "live.day_summary": {
        "label": "实时采集竞彩日汇总",
        "fresh_minutes": 36 * 60,
        "required_for_profiles": [],
    },
    "live.prematch_standings": {
        "label": "实时采集赛前积分榜快照",
        "fresh_minutes": 20,
        "required_for_profiles": [],
    },
    "hist.scan_gaps": {
        "label": "历史补数扫缺口",
        "fresh_minutes": 36 * 60,
        "required_for_profiles": ["hist", "all"],
    },
    "hist.pull": {
        "label": "历史补数拉数",
        "fresh_minutes": 36 * 60,
        "required_for_profiles": ["hist", "all"],
    },
    "cn.prepare": {
        "label": "大陆站打包",
        "fresh_minutes": 36 * 60,
        "required_for_profiles": ["cn", "all"],
    },
    "cn.push": {
        "label": "大陆站推送",
        "fresh_minutes": 36 * 60,
        "required_for_profiles": ["cn", "all"],
    },
    "fc3d.run": {
        "label": "福彩 3D 晚间跑完",
        "fresh_minutes": 36 * 60,
        "required_for_profiles": ["daily", "all"],
    },
    "backup.daily": {
        "label": "正式库每日备份",
        "fresh_minutes": 36 * 60,
        "required_for_profiles": ["daily", "all"],
    },
}

ALLOWED_STATUS = frozenset({"ok", "fail", "partial", "skipped"})


def marks_root() -> Path:
    raw = (os.environ.get("STAGE_MARKS_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path("/workspace/odds-data/stage-marks")


def latest_dir(root: Path | None = None) -> Path:
    return (root or marks_root()) / "latest"


def log_dir(root: Path | None = None) -> Path:
    return (root or marks_root()) / "log"


def now_cn() -> datetime:
    return datetime.now(TZ_CN)


def iso_cn(dt: datetime | None = None) -> str:
    d = dt or now_cn()
    if d.tzinfo is None:
        d = d.replace(tzinfo=TZ_CN)
    return d.astimezone(TZ_CN).isoformat(timespec="seconds")


def _safe_stage_filename(stage: str) -> str:
    if not stage or any(c in stage for c in "/\\"):
        raise ValueError(f"invalid stage name: {stage!r}")
    return f"{stage}.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def write_stage_mark(
    stage: str,
    *,
    status: str = "ok",
    summary: dict[str, Any] | None = None,
    source: str | None = None,
    refs: dict[str, Any] | None = None,
    finished_at: str | None = None,
    extra: dict[str, Any] | None = None,
    root: Path | None = None,
    also_log: bool = True,
) -> Path:
    """写入（覆盖）latest/<stage>.json；可选追加到 log/<stage>.jsonl。

    status 仅允许 ok / fail / partial / skipped。
    """
    status = str(status).strip().lower()
    if status not in ALLOWED_STATUS:
        raise ValueError(f"status must be one of {sorted(ALLOWED_STATUS)}, got {status!r}")
    if stage not in STAGE_DEFS:
        # 允许扩展阶段名，但建议登记；未登记仍可写
        pass

    root = root or marks_root()
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "stage": stage,
        "status": status,
        "finished_at": finished_at or iso_cn(),
        "tz": TZ_NAME,
        "summary": summary or {},
        "source": source or "",
        "refs": refs or {},
    }
    if extra:
        # 不覆盖核心键
        for k, v in extra.items():
            if k not in payload:
                payload[k] = v

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    latest = latest_dir(root) / _safe_stage_filename(stage)
    _atomic_write(latest, text)

    if also_log:
        log_path = log_dir(root) / f"{stage}.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return latest


def read_stage_mark(stage: str, root: Path | None = None) -> dict[str, Any] | None:
    path = latest_dir(root or marks_root()) / _safe_stage_filename(stage)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_latest_marks(root: Path | None = None) -> dict[str, dict[str, Any]]:
    d = latest_dir(root or marks_root())
    out: dict[str, dict[str, Any]] = {}
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stage = obj.get("stage") or p.stem
        out[stage] = obj
    return out


def parse_finished_at(mark: dict[str, Any]) -> datetime | None:
    s = mark.get("finished_at")
    if not s or not isinstance(s, str):
        return None
    try:
        # 支持 2026-11-07T17:27:42+08:00
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_CN)
        return dt.astimezone(TZ_CN)
    except ValueError:
        return None


def age_minutes(mark: dict[str, Any], *, now: datetime | None = None) -> float | None:
    fin = parse_finished_at(mark)
    if fin is None:
        return None
    n = now or now_cn()
    return max(0.0, (n - fin).total_seconds() / 60.0)


def is_fresh(
    mark: dict[str, Any] | None,
    *,
    fresh_minutes: float | None = None,
    now: datetime | None = None,
) -> bool:
    if not mark:
        return False
    if mark.get("status") == "fail":
        return False
    mins = fresh_minutes
    if mins is None:
        mins = float(STAGE_DEFS.get(mark.get("stage", ""), {}).get("fresh_minutes", 36 * 60))
    age = age_minutes(mark, now=now)
    if age is None:
        return False
    return age <= float(mins)
