"""阶段 B1 的读适配：白名单转发、单场精简与上游错误映射。

上游由 httpx 替身提供，不连接本机 8787。
"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from openclaw_gateway import upstream as upstream_mod
from openclaw_gateway.config import UPSTREAM_ORIGIN, upstream_max_bytes, upstream_timeout_seconds
from openclaw_gateway.upstream import (
    USER_AGENT,
    is_safe_match_id,
    match_upstream_url,
    open_client as real_open_client,
)

_SECRET = "upstream-secret-must-not-leak"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeResponse:
    """最小的流式响应。正文按小块交出，便于验证大小上限。"""

    def __init__(
        self,
        status_code: int,
        content: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._content = content
        self.read_started = False

    def iter_bytes(self, chunk_size: int | None = None):
        self.read_started = True
        step = 7
        for index in range(0, len(self._content), step):
            yield self._content[index : index + step]

    def close(self) -> None:
        return None


class _Stream:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response

    def __enter__(self) -> FakeResponse:
        return self._response

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._response.close()
        return False


def install_upstream(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeResponse | None = None,
    error: BaseException | None = None,
) -> list[dict[str, object]]:
    """用替身替换上游客户端，并记录每一次打开和请求。"""
    calls: list[dict[str, object]] = []

    def open_client(timeout_seconds: float):
        record: dict[str, object] = {"timeout_seconds": timeout_seconds}
        calls.append(record)

        class _Client:
            def __enter__(self) -> _Client:
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def stream(self, method, url, params=None, headers=None):
                record["method"] = method
                record["url"] = url
                record["params"] = params
                record["headers"] = dict(headers or {})
                if error is not None:
                    raise error
                if response is None:
                    raise AssertionError("测试没有准备上游响应")
                return _Stream(response)

        return _Client()

    monkeypatch.setattr(upstream_mod, "open_client", open_client)
    return calls


def _json_response(status: int, payload: object, headers: dict[str, str] | None = None) -> FakeResponse:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return FakeResponse(status, raw, headers)


def _list_payload() -> dict[str, object]:
    return {
        "date": "2026-10-27",
        "scope": "jingcai",
        "items": [
            {
                "id": "2026-10-27|一001",
                "match": {
                    "teams": {"home": "主队", "away": "客队"},
                    "home_team_aliases": ["列表别名保留"],
                    "teams_pending_confirm": {"home": True, "away": False},
                },
                "has_prediction": True,
                "direction": "主队",
                "result": None,
                "strategies": ["CFFXDJ_5_V3"],
                "picks": [{"market": "ah", "side": "主队"}],
            }
        ],
    }


def _detail_payload() -> dict[str, object]:
    return {
        "match": {
            "date": "2026-10-09",
            "weekday": "五",
            "scope": "jingcai",
            "jc": {"id": "五001", "weekday": "五", "no": "001", "extra": "丢"},
            "competition": {
                "name": "示例联赛",
                "canonical": "示例联赛",
                "type": "联赛",
                "stage": "常规",
                "league_pending_confirm": True,
            },
            "kickoff_hour": 19,
            "kickoff_at": "2026-10-09T19:35:00+08:00",
            "kickoff_minute_known": True,
            "teams": {
                "home": "主队原文",
                "away": "客队原文",
                "home_canonical": "主队常用名",
                "away_canonical": "客队常用名",
                "home_team_aliases": ["不应出现的别名"],
            },
            "teams_pending_confirm": {"home": False, "away": True},
            "league_pending_confirm": True,
            "home_team_aliases": ["不应出现的别名"],
            "away_team_aliases": ["客队别名"],
            "home_team_pending_confirm": False,
            "away_team_pending_confirm": True,
            "home_team_canonical": "主队常用名",
            "away_team_canonical": "客队常用名",
            "league_canonical": "示例联赛",
            "home_team_id": 11,
            "away_team_id": 22,
            "league_id": 33,
            "ids": {"5df_fixture_id": "secret-fixture"},
        },
        "result": {
            "home_goals": 1,
            "away_goals": 0,
            "total_goals": 1,
            "wdl": "H",
            "secret_note": _SECRET,
        },
        "stats": {"recent": {"secret": _SECRET}},
        "odds": {"asian": {"secret": _SECRET}},
        "meta": {"source_file": _SECRET},
        "has_prediction": True,
        "direction": "主队",
        "strategies": ["CFFXDJ_5_V3"],
        "picks": [{"market": "ah"}],
    }


def test_upstream_origin_stays_on_loopback() -> None:
    assert UPSTREAM_ORIGIN == "http://127.0.0.1:8787"


def test_real_open_client_disables_proxy_and_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Capture:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(upstream_mod.httpx, "Client", _Capture)
    real_open_client(3)
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 3.0
    assert timeout.read == 3.0


def test_timeout_and_max_bytes_defaults_caps_and_do_not_echo(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("OPENCLAW_UPSTREAM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("OPENCLAW_UPSTREAM_MAX_BYTES", raising=False)
    assert upstream_timeout_seconds() == 3.0
    assert upstream_max_bytes() == 1_048_576

    monkeypatch.setenv("OPENCLAW_UPSTREAM_TIMEOUT_SECONDS", "4.5")
    assert upstream_timeout_seconds() == 4.5
    monkeypatch.setenv("OPENCLAW_UPSTREAM_TIMEOUT_SECONDS", "99")
    assert upstream_timeout_seconds() == 10.0
    monkeypatch.setenv("OPENCLAW_UPSTREAM_MAX_BYTES", "128")
    assert upstream_max_bytes() == 128
    monkeypatch.setenv("OPENCLAW_UPSTREAM_MAX_BYTES", "2000000")
    assert upstream_max_bytes() == 1_048_576

    from openclaw_gateway import config as config_mod

    config_mod._warned_config.clear()
    leaked = "leaked-timeout-secret"
    monkeypatch.setenv("OPENCLAW_UPSTREAM_TIMEOUT_SECONDS", leaked)
    with caplog.at_level(logging.WARNING):
        assert upstream_timeout_seconds() == 3.0
    assert leaked not in caplog.text


def test_match_upstream_url_quotes_one_segment() -> None:
    assert match_upstream_url("42") == "http://127.0.0.1:8787/matches/42"
    assert (
        match_upstream_url("2026-10-09|五001")
        == "http://127.0.0.1:8787/matches/2026-10-09%7C%E4%BA%94001"
    )
    assert is_safe_match_id("2026-10-09|五001") is True
    assert is_safe_match_id("42") is True
    assert is_safe_match_id("..") is False
    assert is_safe_match_id("a/b") is False
    assert is_safe_match_id("a b") is False
    assert is_safe_match_id("a" * 201) is False


def test_list_and_match_without_token_do_not_call_upstream(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    calls = install_upstream(monkeypatch, _json_response(200, _list_payload()))
    missing_list = client.get("/v1/read/matches", params={"date": "2026-10-27"})
    missing_match = client.get("/v1/read/matches/2026-10-27|一001")
    assert missing_list.status_code == 401
    assert missing_match.status_code == 401
    assert missing_list.json()["error"]["code"] == "unauthorized"
    assert _list_payload()["date"] not in missing_list.text
    assert calls == []


def test_capabilities_still_lists_read_routes(client: TestClient, token: str) -> None:
    response = client.get("/v1/capabilities", headers=_auth(token))
    body = response.json()
    assert response.status_code == 200
    assert body["stage"] == "b2"
    names = [item["name"] for item in body["capabilities"]]
    assert names[:2] == ["read.matches", "read.match"]


def test_list_passthrough_and_query_whitelist(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    payload = _list_payload()
    calls = install_upstream(monkeypatch, _json_response(200, payload))
    response = client.get(
        "/v1/read/matches",
        params={"scope": "all", "date": "2026-10-27", "raw": "1", "note": _SECRET},
        headers=_auth(token),
    )
    assert response.status_code == 200
    # 替身正文原样返回，不按查询串改写 scope，也不裁列表里的别名。
    assert response.json() == payload
    assert response.headers["cache-control"] == "no-store"
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == "http://127.0.0.1:8787/matches"
    assert calls[0]["params"] == {"date": "2026-10-27", "scope": "all"}
    assert calls[0]["headers"] == {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    assert calls[0]["timeout_seconds"] == 3.0
    assert _SECRET not in json.dumps(calls, ensure_ascii=False)
    assert "predictions" not in str(calls[0]["url"])


def test_list_omits_scope_when_absent(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    calls = install_upstream(monkeypatch, _json_response(200, {"date": "2026-10-27", "items": []}))
    response = client.get(
        "/v1/read/matches",
        params={"date": "2026-10-27"},
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert calls[0]["params"] == {"date": "2026-10-27"}


@pytest.mark.parametrize(
    ("params", "message_part"),
    [
        ({}, "date 必填"),
        ({"date": "2026-10-27", "date_dup": "1"}, "date 必填"),
        ({"date": "20261027"}, "有效日期"),
        ({"date": "2026-02-31"}, "有效日期"),
        ({"date": "2026-10-27", "scope": "extended"}, "jingcai"),
        ({"date": "today"}, "有效日期"),
    ],
)
def test_list_rejects_bad_query_without_upstream(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    token: str,
    params: dict[str, str],
    message_part: str,
) -> None:
    calls = install_upstream(monkeypatch, _json_response(200, {"items": []}))
    # date 重复用原始查询串表达，params 字典无法放两个同名键。
    if "date_dup" in params:
        response = client.get(
            "/v1/read/matches?date=2026-10-27&date=2026-10-28",
            headers=_auth(token),
        )
    else:
        response = client.get("/v1/read/matches", params=params, headers=_auth(token))
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "bad_request"
    assert message_part in body["error"]["message"]
    assert calls == []
    assert response.headers["cache-control"] == "no-store"


def test_match_is_slim_and_does_not_fetch_predictions(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    calls = install_upstream(monkeypatch, _json_response(200, _detail_payload()))
    match_id = "2026-10-09|五001"
    response = client.get(
        "/v1/read/matches/" + quote(match_id, safe=""),
        params={"strategy": "CFFXDJ_5_V3", "raw": "true"},
        headers={**_auth(token), "Authorization": f"Bearer {token}", "X-Debug": _SECRET},
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": match_id,
        "match": {
            "date": "2026-10-09",
            "weekday": "五",
            "scope": "jingcai",
            "kickoff_hour": 19,
            "kickoff_at": "2026-10-09T19:35:00+08:00",
            "kickoff_minute_known": True,
            "jc": {"id": "五001", "weekday": "五", "no": "001"},
            "competition": {
                "name": "示例联赛",
                "canonical": "示例联赛",
                "type": "联赛",
                "stage": "常规",
            },
            "teams": {
                "home": "主队原文",
                "away": "客队原文",
                "home_canonical": "主队常用名",
                "away_canonical": "客队常用名",
            },
        },
        "result": {"home_goals": 1, "away_goals": 0, "total_goals": 1, "wdl": "H"},
    }
    text = response.text
    for forbidden in (
        "stats",
        "odds",
        "meta",
        "has_prediction",
        "direction",
        "strategies",
        "picks",
        "aliases",
        "pending",
        "secret-fixture",
        _SECRET,
        "不应出现的别名",
    ):
        assert forbidden not in text
    assert len(calls) == 1
    assert calls[0]["url"] == "http://127.0.0.1:8787/matches/2026-10-09%7C%E4%BA%94001"
    assert calls[0]["params"] is None
    assert "predictions" not in str(calls[0]["url"])
    assert calls[0]["headers"] == {
        "Accept": "application/json",
        "User-Agent": "football-openclaw-gateway/b1",
    }
    assert token not in json.dumps(calls[0]["headers"])
    assert response.headers["cache-control"] == "no-store"


def test_numeric_match_id_is_forwarded(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    payload = _detail_payload()
    calls = install_upstream(monkeypatch, _json_response(200, payload))
    response = client.get("/v1/read/matches/42", headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["id"] == "42"
    assert calls[0]["url"] == "http://127.0.0.1:8787/matches/42"


def test_unknown_match_is_404_without_upstream_body(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str, caplog: pytest.LogCaptureFixture
) -> None:
    upstream = FakeResponse(404, json.dumps({"detail": _SECRET, "trace": "stack"}).encode("utf-8"))
    install_upstream(monkeypatch, upstream)
    with caplog.at_level(logging.DEBUG):
        response = client.get("/v1/read/matches/missing-uid", headers=_auth(token))
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "比赛不存在。"}}
    assert _SECRET not in response.text
    assert "stack" not in response.text
    assert upstream.read_started is False
    assert _SECRET not in caplog.text
    assert response.headers["cache-control"] == "no-store"


def test_list_upstream_404_uses_not_found(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    install_upstream(monkeypatch, FakeResponse(404, b'{"detail":"' + _SECRET.encode() + b'"}'))
    response = client.get(
        "/v1/read/matches",
        params={"date": "2026-10-27"},
        headers=_auth(token),
    )
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert "比赛列表" in body["error"]["message"]
    assert _SECRET not in response.text


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (httpx.ReadTimeout(_SECRET), 504, "upstream_timeout"),
        (httpx.ConnectError(_SECRET), 502, "upstream_error"),
    ],
)
def test_timeout_and_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    token: str,
    caplog: pytest.LogCaptureFixture,
    error: BaseException,
    status: int,
    code: str,
) -> None:
    install_upstream(monkeypatch, error=error)
    with caplog.at_level(logging.DEBUG):
        response = client.get(
            "/v1/read/matches",
            params={"date": "2026-10-27"},
            headers=_auth(token),
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert _SECRET not in response.text
    assert _SECRET not in caplog.text


def test_upstream_5xx_and_redirect_become_upstream_error(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str, caplog: pytest.LogCaptureFixture
) -> None:
    install_upstream(
        monkeypatch,
        FakeResponse(
            500,
            f'{{"trace":"{_SECRET}"}}'.encode("utf-8"),
            {"x-secret": _SECRET, "location": "http://evil.example/secret"},
        ),
    )
    with caplog.at_level(logging.DEBUG):
        failed = client.get(
            "/v1/read/matches/7",
            headers=_auth(token),
        )
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "upstream_error"
    assert _SECRET not in failed.text
    assert "evil.example" not in failed.text
    assert "x-secret" not in {key.lower() for key in failed.headers}
    assert _SECRET not in caplog.text

    install_upstream(
        monkeypatch,
        FakeResponse(302, b"", {"location": "http://evil.example/" + _SECRET}),
    )
    redirected = client.get("/v1/read/matches/7", headers=_auth(token))
    assert redirected.status_code == 502
    assert redirected.json()["error"]["code"] == "upstream_error"
    assert _SECRET not in redirected.text
    assert "evil.example" not in redirected.text


def test_upstream_4xx_becomes_client_error(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    install_upstream(
        monkeypatch,
        FakeResponse(422, json.dumps({"detail": _SECRET}).encode("utf-8")),
    )
    response = client.get(
        "/v1/read/matches",
        params={"date": "2026-10-27", "scope": "jingcai"},
        headers=_auth(token),
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_client_error"
    assert _SECRET not in response.text


def test_oversized_upstream_body_is_rejected(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    monkeypatch.setenv("OPENCLAW_UPSTREAM_MAX_BYTES", "32")
    declared = FakeResponse(200, b'{"items":[]}', {"content-length": "999999"})
    install_upstream(monkeypatch, declared)
    by_header = client.get(
        "/v1/read/matches",
        params={"date": "2026-10-27"},
        headers=_auth(token),
    )
    assert by_header.status_code == 502
    assert by_header.json()["error"]["code"] == "upstream_response_too_large"
    assert declared.read_started is False

    chunked = FakeResponse(200, (_SECRET * 20).encode("utf-8"))
    install_upstream(monkeypatch, chunked)
    by_stream = client.get("/v1/read/matches/9", headers=_auth(token))
    assert by_stream.status_code == 502
    assert by_stream.json()["error"]["code"] == "upstream_response_too_large"
    assert _SECRET not in by_stream.text


def test_non_json_upstream_is_upstream_error(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    install_upstream(monkeypatch, FakeResponse(200, f"<html>{_SECRET}</html>".encode("utf-8")))
    response = client.get("/v1/read/matches/9", headers=_auth(token))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
    assert _SECRET not in response.text


def test_unregistered_read_paths_stay_not_implemented(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    calls = install_upstream(monkeypatch, _json_response(200, {"items": []}))
    paths = (
        "/v1/read/matches/42/odds",
        "/v1/read/matches/dates",
        "/v1/read/matches/42/predictions",
        "/v1/read/strategies",
        "/v1/read",
    )
    bare = client.get("/v1/read/matches/42/odds")
    assert bare.status_code == 401
    for path in paths:
        response = client.get(path, headers=_auth(token))
        assert response.status_code == 501, path
        assert response.json()["error"]["code"] == "not_implemented"
    jobs = client.post(
        "/v1/jobs",
        headers=_auth(token),
        json={"type": "ping", "payload": {"secret": _SECRET}},
    )
    assert jobs.status_code == 400
    assert jobs.json()["error"]["code"] == "bad_request"
    assert _SECRET not in jobs.text
    assert calls == []


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_write_methods_are_not_forwarded(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str, method: str
) -> None:
    calls = install_upstream(monkeypatch, _json_response(200, {"items": []}))
    body = json.dumps({"secret": _SECRET}).encode("utf-8")
    for path in ("/v1/read/matches", "/v1/read/matches/42", "/v1/read/matches/42/odds"):
        response = client.request(
            method.upper(),
            path,
            headers={**_auth(token), "Content-Type": "application/json"},
            content=body,
        )
        assert response.status_code == 501, (method, path)
        assert response.json()["error"]["code"] == "not_implemented"
        assert _SECRET not in response.text
    assert calls == []


def test_unsafe_match_id_does_not_call_upstream(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    calls = install_upstream(monkeypatch, _json_response(200, _detail_payload()))
    spaced = client.get("/v1/read/matches/" + quote("bad id"), headers=_auth(token))
    assert spaced.status_code == 404
    assert spaced.json()["error"] == {"code": "not_found", "message": "比赛不存在。"}
    long_id = client.get("/v1/read/matches/" + ("a" * 201), headers=_auth(token))
    assert long_id.status_code == 404
    assert calls == []


def test_timeout_env_is_passed_to_upstream_call(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    monkeypatch.setenv("OPENCLAW_UPSTREAM_TIMEOUT_SECONDS", "99")
    calls = install_upstream(monkeypatch, _json_response(200, {"date": "2026-10-27", "items": []}))
    response = client.get(
        "/v1/read/matches",
        params={"date": "2026-10-27"},
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert calls[0]["timeout_seconds"] == 10.0


def test_non_object_match_payload_is_upstream_error(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    install_upstream(monkeypatch, _json_response(200, ["not-an-object", _SECRET]))
    response = client.get("/v1/read/matches/9", headers=_auth(token))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
    assert _SECRET not in response.text
