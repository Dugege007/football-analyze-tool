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
