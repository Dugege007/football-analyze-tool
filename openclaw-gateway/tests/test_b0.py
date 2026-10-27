"""阶段 B0 的路由、鉴权与启动约束。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openclaw_gateway.config import assert_loopback_bind, gateway_bind, gateway_port
from openclaw_gateway.main import handle_unexpected


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_without_token_is_public(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "football-openclaw-gateway",
        "version": "0.1.0-b1",
        "stage": "b1",
    }
    assert response.headers["cache-control"] == "no-store"


def test_health_ignores_authorization_when_auth_disabled(client: TestClient) -> None:
    response = client.get("/v1/health", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.parametrize("flag", ["1", "true", "yes", "on"])
def test_health_requires_token_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    token: str,
    flag: str,
) -> None:
    monkeypatch.setenv("GATEWAY_REQUIRE_AUTH_ON_HEALTH", flag)
    missing = client.get("/v1/health")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "unauthorized"
    ok = client.get("/v1/health", headers=_auth(token))
    assert ok.status_code == 200
    assert ok.json()["stage"] == "b1"


def test_health_unknown_flag_fails_closed(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("GATEWAY_REQUIRE_AUTH_ON_HEALTH", "maybe")
    response = client.get("/v1/health")
    assert response.status_code == 401


def test_capabilities_without_token_is_unauthorized(client: TestClient) -> None:
    response = client.get("/v1/capabilities")
    assert response.status_code == 401
    body = response.json()
    assert body == {
        "error": {
            "code": "unauthorized",
            "message": "缺少 Bearer 令牌，或令牌与服务器配置不一致。",
        }
    }
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert "test-token-correct" not in response.text


def test_capabilities_with_wrong_or_malformed_token(client: TestClient, token: str) -> None:
    wrong = client.get("/v1/capabilities", headers=_auth(token + "-nope"))
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "unauthorized"
    basic = client.get("/v1/capabilities", headers={"Authorization": f"Basic {token}"})
    assert basic.status_code == 401
    empty = client.get("/v1/capabilities", headers={"Authorization": "Bearer"})
    assert empty.status_code == 401


def test_capabilities_with_token_lists_two_read_routes(client: TestClient, token: str) -> None:
    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": f"bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "stage": "b1",
        "capabilities": [
            {"name": "read.matches", "methods": ["GET /v1/read/matches"]},
            {"name": "read.match", "methods": ["GET /v1/read/matches/{match_id}"]},
        ],
    }


def test_capabilities_rejects_empty_configured_token(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("OPENCLAW_BEARER_TOKEN", "   ")
    response = client.get("/v1/capabilities", headers={"Authorization": "Bearer    "})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_post_jobs_with_token_is_not_implemented(client: TestClient, token: str) -> None:
    response = client.post(
        "/v1/jobs",
        headers={**_auth(token), "Content-Type": "application/json"},
        json={
            "type": "ping",
            "payload": {"secret": "do-not-echo"},
            "idempotency_key": None,
            "client_ref": "ref-1",
        },
    )
    assert response.status_code == 501
    assert response.json() == {
        "error": {
            "code": "not_implemented",
            "message": "该接口已列入规划，但在当前阶段（b1）尚未实现。",
        }
    }
    assert "do-not-echo" not in response.text
    assert "ping" not in response.text


def test_post_jobs_without_token_is_unauthorized(client: TestClient) -> None:
    response = client.post("/v1/jobs", json={"type": "ping", "payload": {}})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_get_job_with_token_is_not_implemented(client: TestClient, token: str) -> None:
    response = client.get("/v1/jobs/job_123", headers=_auth(token))
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_implemented"


def test_unregistered_read_routes_require_token_then_return_not_implemented(
    client: TestClient, token: str
) -> None:
    missing = client.get("/v1/read/matches/42/odds")
    assert missing.status_code == 401
    for path in ("/v1/read", "/v1/read/matches/42/odds", "/v1/read/strategies"):
        response = client.get(path, headers=_auth(token))
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "not_implemented"
    posted = client.post("/v1/read/matches", headers=_auth(token), json={})
    assert posted.status_code == 501


def test_unknown_path_and_wrong_method(client: TestClient, token: str) -> None:
    unknown = client.get("/v1/does-not-exist", headers=_auth(token))
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"
    # POST /v1/jobs 已注册，因此 GET 属于方法不对，而不是未知路径。
    job_list = client.get("/v1/jobs", headers=_auth(token))
    assert job_list.status_code == 405
    assert job_list.json()["error"]["code"] == "method_not_allowed"
    wrong_method = client.put("/v1/jobs")
    assert wrong_method.status_code == 405
    assert wrong_method.json() == {
        "error": {
            "code": "method_not_allowed",
            "message": "该路径不支持当前的 HTTP 方法。",
        }
    }
    assert client.get("/v1/health/").status_code == 404
    docs = client.get("/docs")
    assert docs.status_code == 404


def test_unhandled_exception_response_hides_text() -> None:
    from openclaw_gateway.main import app

    @app.get("/v1/_test_boom")
    def _boom() -> dict[str, str]:
        raise RuntimeError("boom-detail-must-not-leak")

    # 服务器在写完 500 正文后仍会把异常抛给访问日志。测试客户端默认把这次抛出当成失败。
    with TestClient(app, raise_server_exceptions=False) as quiet:
        response = quiet.get("/v1/_test_boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "boom-detail-must-not-leak" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_internal_error_hides_exception_text() -> None:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/v1/health",
        "raw_path": b"/v1/health",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8790),
    }
    from starlette.requests import Request

    response = handle_unexpected(
        Request(scope),
        RuntimeError("detail-that-must-stay-in-the-log"),
    )
    assert response.status_code == 500
    assert response.body
    text = bytes(response.body).decode("utf-8")
    assert "detail-that-must-stay-in-the-log" not in text
    assert "internal_error" in text


def test_bind_rejects_public_addresses_without_echoing_them() -> None:
    secret = "bind-value-must-not-be-echoed"
    with pytest.raises(SystemExit) as caught:
        assert_loopback_bind(secret)
    assert secret not in str(caught.value)
    for host in ("0.0.0.0", "::", "192.168.1.10", "[::]"):
        with pytest.raises(SystemExit):
            assert_loopback_bind(host)
    assert assert_loopback_bind("127.0.0.1") == "127.0.0.1"
    assert assert_loopback_bind("[::1]") == "::1"
    assert assert_loopback_bind("LocalHost") == "localhost"


def test_port_defaults_and_rejects_api_port_without_echoing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GATEWAY_PORT", raising=False)
    assert gateway_port() == 8790
    monkeypatch.setenv("GATEWAY_PORT", "8791")
    assert gateway_port() == 8791
    monkeypatch.setenv("GATEWAY_PORT", "8787")
    with pytest.raises(SystemExit) as reserved:
        gateway_port()
    assert "8787" in str(reserved.value)
    leaked = "not-a-port-secret"
    monkeypatch.setenv("GATEWAY_PORT", leaked)
    with pytest.raises(SystemExit) as caught:
        gateway_port()
    assert leaked not in str(caught.value)


def test_gateway_bind_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATEWAY_BIND", raising=False)
    assert gateway_bind() == "127.0.0.1"


def test_main_refuses_public_bind_before_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_BIND", "0.0.0.0")
    import uvicorn

    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("非回环地址不应启动 uvicorn")

    monkeypatch.setattr(uvicorn, "run", _fail_if_called)
    from openclaw_gateway.__main__ import main

    with pytest.raises(SystemExit):
        main()


def test_main_passes_loopback_to_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_BIND", "127.0.0.1")
    monkeypatch.setenv("GATEWAY_PORT", "8791")
    monkeypatch.setenv("OPENCLAW_BEARER_TOKEN", "x" * 32)
    import uvicorn

    captured: dict[str, object] = {}

    def _capture(app, **kwargs):
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", _capture)
    from openclaw_gateway.__main__ import main

    main()
    assert captured["kwargs"]["host"] == "127.0.0.1"
    assert captured["kwargs"]["port"] == 8791
    assert captured["kwargs"]["proxy_headers"] is False
