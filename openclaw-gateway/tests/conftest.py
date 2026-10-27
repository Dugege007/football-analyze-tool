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


@pytest.fixture
def token() -> str:
    return TEST_TOKEN


@pytest.fixture
def client() -> TestClient:
    from openclaw_gateway.main import app

    return TestClient(app)
