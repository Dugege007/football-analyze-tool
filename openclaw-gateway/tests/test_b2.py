"""阶段 B2 的最小任务通道：鉴权、ping、幂等与查询。"""

from __future__ import annotations

import os
import re
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from openclaw_gateway.config import (
    job_create_timeout_seconds,
    job_db_path,
    job_max_seconds,
    job_retention_seconds,
)
from openclaw_gateway.jobs import close_connections, execute_whitelisted

_ISO_BEIJING = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post(client: TestClient, token: str, body: dict[str, object]):
    return client.post("/v1/jobs", headers=_auth(token), json=body)


def _entered_running(job_id: str) -> int:
    conn = sqlite3.connect(os.environ["OPENCLAW_JOB_DB_PATH"])
    try:
        row = conn.execute(
            "SELECT status, entered_running FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] in {"succeeded", "failed"}
    return int(row[1])


def test_health_reports_b2(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "b2"
    assert body["version"] == "0.1.0-b2"
    assert response.headers["cache-control"] == "no-store"


def test_capabilities_include_job_routes(client: TestClient, token: str) -> None:
    response = client.get("/v1/capabilities", headers=_auth(token))
    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "b2"
    assert body["capabilities"] == [
        {"name": "read.matches", "methods": ["GET /v1/read/matches"]},
        {"name": "read.match", "methods": ["GET /v1/read/matches/{match_id}"]},
        {"name": "jobs.create", "methods": ["POST /v1/jobs"]},
        {"name": "jobs.get", "methods": ["GET /v1/jobs/{id}"]},
    ]


def test_job_routes_require_bearer(client: TestClient, token: str) -> None:
    missing = client.post("/v1/jobs", json={"type": "ping", "payload": {"echo": "hidden"}})
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "unauthorized"
    assert "hidden" not in missing.text
    wrong = _post(
        client,
        token + "-nope",
        {
            "type": "ping",
            "payload": {"echo": "hidden"},
            "idempotency_key": "auth-key",
        },
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "unauthorized"
    assert "hidden" not in wrong.text
    lookup = client.get("/v1/jobs/job_missing", headers=_auth(token + "-nope"))
    assert lookup.status_code == 401
    assert lookup.json()["error"]["code"] == "unauthorized"
    # 鉴权失败不得入队。若错误令牌已经写入同一幂等键，这里会因正文不同而返回 409。
    created = _post(
        client,
        token,
        {
            "type": "ping",
            "payload": {"echo": "visible"},
            "idempotency_key": "auth-key",
        },
    )
    assert created.status_code == 202
    assert created.json()["result"]["echo"] == "visible"


def test_unknown_type_is_rejected_without_enqueue(client: TestClient, token: str) -> None:
    rejected = _post(
        client,
        token,
        {
            "type": "not_a_real_type",
            "payload": {},
            "idempotency_key": "type-key",
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "bad_request"
    assert "不支持的任务类型" in rejected.json()["error"]["message"]
    assert "not_a_real_type" not in rejected.text
    created = _post(
        client,
        token,
        {"type": "ping", "payload": {}, "idempotency_key": "type-key"},
    )
    assert created.status_code == 202
    assert created.json()["type"] == "ping"


def test_ping_succeeds_synchronously_and_can_be_read(
    client: TestClient, token: str
) -> None:
    created = _post(
        client,
        token,
        {
            "type": "ping",
            "payload": {"echo": "hello"},
            "client_ref": "qq-msg-123",
            "idempotency_key": "oc-20261027-001",
        },
    )
    assert created.status_code == 202
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    assert body["id"].startswith("job_")
    assert len(body["id"]) == len("job_") + 32
    assert body["type"] == "ping"
    assert body["status"] == "succeeded"
    assert body["error"] is None
    assert body["client_ref"] == "qq-msg-123"
    assert body["idempotency_key"] == "oc-20261027-001"
    assert body["result"] == {
        "ok": True,
        "echo": "hello",
        "server_time": body["updated_at"],
    }
    assert _ISO_BEIJING.fullmatch(body["created_at"])
    assert _ISO_BEIJING.fullmatch(body["updated_at"])
    assert body["updated_at"] >= body["created_at"]
    assert _entered_running(body["id"]) == 1

    fetched = client.get(f"/v1/jobs/{body['id']}", headers=_auth(token))
    assert fetched.status_code == 200
    assert fetched.headers["cache-control"] == "no-store"
    assert fetched.json() == body


def test_ping_without_echo_returns_null_echo(client: TestClient, token: str) -> None:
    created = _post(client, token, {"type": "ping", "payload": {}})
    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "succeeded"
    assert body["result"]["ok"] is True
    assert body["result"]["echo"] is None
    assert body["client_ref"] is None
    assert body["idempotency_key"] is None


def test_idempotency_replays_same_canonical_request(client: TestClient, token: str) -> None:
    first = _post(
        client,
        token,
        {
            "type": "ping",
            "payload": {"echo": "same"},
            "idempotency_key": "replay-1",
            "client_ref": "first",
        },
    )
    second = _post(
        client,
        token,
        {
            "type": "ping",
            "payload": {"echo": "same"},
            "idempotency_key": "replay-1",
            "client_ref": "second",
        },
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["client_ref"] == "first"
    assert second.json() == first.json()
    other = _post(client, token, {"type": "ping", "payload": {"echo": "same"}})
    assert other.status_code == 202
    assert other.json()["id"] != first.json()["id"]


def test_idempotency_conflict_does_not_overwrite(client: TestClient, token: str) -> None:
    first = _post(
        client,
        token,
        {
            "type": "ping",
            "payload": {"echo": "one"},
            "idempotency_key": "conflict-1",
        },
    )
    conflict = _post(
        client,
        token,
        {
            "type": "ping",
            "payload": {"echo": "two"},
            "idempotency_key": "conflict-1",
        },
    )
    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert conflict.headers["cache-control"] == "no-store"
    fetched = client.get(f"/v1/jobs/{first.json()['id']}", headers=_auth(token))
    assert fetched.status_code == 200
    assert fetched.json()["result"]["echo"] == "one"


def test_missing_job_is_not_found(client: TestClient, token: str) -> None:
    response = client.get("/v1/jobs/job_does_not_exist", headers=_auth(token))
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "任务不存在。"}
    }


def test_terminal_jobs_expire_and_idempotency_key_can_be_reused(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    monkeypatch.setenv("OPENCLAW_JOB_RETENTION_SECONDS", "0")
    body = {"type": "ping", "payload": {"echo": "gone"}, "idempotency_key": "expire-1"}
    first = _post(client, token, body)
    assert first.status_code == 202
    missing = client.get(f"/v1/jobs/{first.json()['id']}", headers=_auth(token))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    second = _post(client, token, body)
    assert second.status_code == 202
    assert second.json()["id"] != first.json()["id"]


def test_job_timeout_is_failed_snapshot(monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str) -> None:
    monkeypatch.setenv("OPENCLAW_JOB_MAX_SECONDS", "0.05")
    monkeypatch.setenv("OPENCLAW_JOB_CREATE_TIMEOUT_SECONDS", "5")

    def _slow(_job_type: str, _payload: dict[str, object]) -> None:
        time.sleep(0.25)

    monkeypatch.setattr("openclaw_gateway.jobs.execute_whitelisted", _slow)
    created = _post(client, token, {"type": "ping", "payload": {"echo": "slow"}})
    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "failed"
    assert body["result"] is None
    assert body["error"]["code"] == "job_timeout"
    fetched = client.get(f"/v1/jobs/{body['id']}", headers=_auth(token))
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "failed"
    assert fetched.json()["error"]["code"] == "job_timeout"


def test_create_timeout_returns_504_and_replay_returns_snapshot(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, token: str
) -> None:
    monkeypatch.setenv("OPENCLAW_JOB_CREATE_TIMEOUT_SECONDS", "0.05")

    def _slow(_job_type: str, _payload: dict[str, object]) -> None:
        time.sleep(0.25)

    monkeypatch.setattr("openclaw_gateway.jobs.execute_whitelisted", _slow)
    payload = {"type": "ping", "payload": {}, "idempotency_key": "create-timeout-1"}
    timed_out = _post(client, token, payload)
    assert timed_out.status_code == 504
    assert timed_out.json()["error"]["code"] == "timeout"
    monkeypatch.setattr("openclaw_gateway.jobs.execute_whitelisted", execute_whitelisted)
    replay = _post(client, token, payload)
    assert replay.status_code == 202
    assert replay.json()["status"] == "succeeded"
    assert replay.json()["result"]["ok"] is True


def test_job_env_defaults_and_db_override(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("OPENCLAW_JOB_DB_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_JOB_MAX_SECONDS", raising=False)
    monkeypatch.delenv("OPENCLAW_JOB_CREATE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("OPENCLAW_JOB_RETENTION_SECONDS", raising=False)
    close_connections()
    assert job_db_path() == "/opt/football-data/openclaw-jobs.sqlite3"
    assert job_max_seconds() == 10.0
    assert job_create_timeout_seconds() == 5.0
    assert job_retention_seconds() == 24 * 60 * 60
    override = tmp_path / "custom.sqlite3"
    monkeypatch.setenv("OPENCLAW_JOB_DB_PATH", str(override))
    assert job_db_path() == str(override)
    monkeypatch.setenv("OPENCLAW_JOB_MAX_SECONDS", "100")
    assert job_max_seconds() == 30.0
    monkeypatch.setenv("OPENCLAW_JOB_RETENTION_SECONDS", "nope")
    assert job_retention_seconds() == 24 * 60 * 60


def test_invalid_job_bodies(client: TestClient, token: str) -> None:
    missing_payload = _post(client, token, {"type": "ping"})
    assert missing_payload.status_code == 400
    echo = "x" * 129
    too_long = _post(client, token, {"type": "ping", "payload": {"echo": echo}})
    assert too_long.status_code == 400
    assert echo not in too_long.text
    bad_key = _post(
        client,
        token,
        {"type": "ping", "payload": {}, "idempotency_key": "has space"},
    )
    assert bad_key.status_code == 400
    assert bad_key.json()["error"]["code"] == "bad_request"
    not_json = client.post(
        "/v1/jobs",
        headers={**_auth(token), "Content-Type": "application/json"},
        content=b"[]",
    )
    assert not_json.status_code == 400
    assert not_json.json()["error"]["code"] == "bad_request"
