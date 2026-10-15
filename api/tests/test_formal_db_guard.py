"""并库后一次性脚本的写前保护：拒绝已归档的 v2d3，写正式库前要求已备份确认。"""
from __future__ import annotations

import pytest

from app import formal_db


def test_archived_v2d3_is_refused(monkeypatch):
    monkeypatch.setenv(formal_db.BACKUP_ENV, "1")
    with pytest.raises(SystemExit) as e:
        formal_db.guard_write_target(formal_db.ARCHIVED_V2D3_PATH, writing=True)
    assert "只读归档" in str(e.value)


def test_formal_write_requires_backup_ack(monkeypatch):
    monkeypatch.delenv(formal_db.BACKUP_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        formal_db.guard_write_target(formal_db.FORMAL_DB_PATH, writing=True)
    assert "备份" in str(e.value)
    monkeypatch.setenv(formal_db.BACKUP_ENV, "1")
    assert formal_db.guard_write_target(formal_db.FORMAL_DB_PATH, writing=True) == formal_db.FORMAL_DB_PATH


def test_read_only_run_needs_no_backup(monkeypatch):
    monkeypatch.delenv(formal_db.BACKUP_ENV, raising=False)
    assert formal_db.guard_write_target(formal_db.FORMAL_DB_PATH, writing=False) == formal_db.FORMAL_DB_PATH


def test_other_path_is_not_blocked(tmp_path, monkeypatch):
    monkeypatch.delenv(formal_db.BACKUP_ENV, raising=False)
    p = tmp_path / "x.db"
    assert formal_db.guard_write_target(p, writing=True) == p.resolve()


def test_sync_odds_asian_requires_explicit_db_path():
    """sync_odds_asian_from_snapshot 没有默认库：不传 --db 必须报错退出（退出码 2）并给出清晰提示，避免误写。"""
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "sync_odds_asian_from_snapshot.py"
    proc = subprocess.run([sys.executable, str(script), "fp"], capture_output=True, text=True,
                          env={**__import__("os").environ, "FAT_DISABLE_DOTENV": "1"})
    assert proc.returncode == 2
    assert "必须显式传入 --db" in proc.stderr
