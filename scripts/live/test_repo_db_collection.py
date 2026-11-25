"""收集阶段默认不打开仓库里的正式库或研究副本。"""
import importlib.util
import sqlite3
from pathlib import Path


def _load_collect_guard():
    path = Path(__file__).resolve().parents[1] / "conftest.py"
    spec = importlib.util.spec_from_file_location("collect_guard_reload", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_collection_does_not_connect_existing_db(tmp_path, monkeypatch):
    """关掉 FAT_REFUSE_REPO_DB 后，两个库文件都在也不调用 sqlite3.connect。"""
    monkeypatch.delenv("FAT_REFUSE_REPO_DB", raising=False)
    app = tmp_path / "app.db"
    replica = tmp_path / "v2d3" / "app.db"
    replica.parent.mkdir()
    for path in (app, replica):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE matches(id INTEGER)")
        conn.executemany("INSERT INTO matches(id) VALUES (?)", [(i,) for i in range(60)])
        conn.commit()
        conn.close()
    opened: list[str] = []
    real_connect = sqlite3.connect

    def spy(name, *args, **kwargs):
        opened.append(str(name))
        return real_connect(name, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    module = _load_collect_guard()
    opened.clear()
    module._APP_DB = app
    module._V2D3_DB = replica
    assert module._real_db_ready() is False
    assert opened == []
    monkeypatch.setenv("FAT_REFUSE_REPO_DB", "1")
    assert module._real_db_ready() is False
    assert opened == []
