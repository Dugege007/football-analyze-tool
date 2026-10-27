"""网关单测的公共夹具。令牌只存在于测试进程里。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

TEST_TOKEN = "test-token-correct"


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_BEARER_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("GATEWAY_REQUIRE_AUTH_ON_HEALTH", "0")
    monkeypatch.setenv("GATEWAY_BIND", "127.0.0.1")
    monkeypatch.setenv("GATEWAY_PORT", "8790")


@pytest.fixture(autouse=True)
def _job_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """任务库只写临时文件，避免单测碰到生产默认路径。"""
    monkeypatch.setenv("OPENCLAW_JOB_DB_PATH", str(tmp_path / "openclaw-jobs.sqlite3"))
    yield
    from openclaw_gateway.jobs import close_connections

    close_connections()


@pytest.fixture(autouse=True)
def _block_live_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认禁止单测连接本机 8787。需要上游时由用例替换 open_client。"""
    import openclaw_gateway.upstream as upstream

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("单测禁止连接真实上游")

    monkeypatch.setattr(upstream, "open_client", _blocked)


@pytest.fixture
def token() -> str:
    return TEST_TOKEN


@pytest.fixture
def client() -> TestClient:
    from openclaw_gateway.main import app

    return TestClient(app)
