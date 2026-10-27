"""把已鉴权的只读请求转到本机 8787，并裁剪单场详情。

只允许 GET 两个路径：`/matches` 与 `/matches/{match_id}`。
不转发调用方的 Authorization，不跟随重定向，不走环境变量里的代理。
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from openclaw_gateway.config import (
    UPSTREAM_ORIGIN,
    upstream_max_bytes,
    upstream_timeout_seconds,
)

log = logging.getLogger("openclaw_gateway")

USER_AGENT = "football-openclaw-gateway/b1"
_MAX_MATCH_ID_LENGTH = 200
_MATCH_FIELDS = (
    "date",
    "weekday",
    "scope",
    "kickoff_hour",
    "kickoff_at",
    "kickoff_minute_known",
)
_JC_FIELDS = ("id", "weekday", "no")
_COMPETITION_FIELDS = ("name", "canonical", "type", "stage")
_TEAM_FIELDS = ("home", "away", "home_canonical", "away_canonical")
_RESULT_FIELDS = ("home_goals", "away_goals", "total_goals", "wdl")


class UpstreamFailure(Exception):
    """已映射成网关错误的上游失败。message 不得包含上游正文。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


class _ResponseTooLarge(Exception):
    """响应体超过上限。只在本模块内部使用。"""


def client_options(timeout_seconds: float) -> dict[str, Any]:
    """上游客户端参数：不继承代理环境变量，也不跟随重定向。"""
    return {
        "timeout": httpx.Timeout(timeout_seconds),
        "trust_env": False,
        "follow_redirects": False,
    }


def open_client(timeout_seconds: float) -> httpx.Client:
    """打开一次上游请求使用的客户端。"""
    return httpx.Client(**client_options(timeout_seconds))


def matches_upstream_url() -> str:
    """比赛列表的上游地址。"""
    return f"{UPSTREAM_ORIGIN}/matches"


def is_safe_match_id(match_id: str) -> bool:
    """判断路径段能否作为单场编号拼进上游地址。

    拒绝空值、过长、路径穿越和空白。合法的 match_uid 与数字主键都可以通过。
    """
    if not isinstance(match_id, str) or match_id == "":
        return False
    if len(match_id) > _MAX_MATCH_ID_LENGTH:
        return False
    if match_id in {".", ".."}:
        return False
    for char in match_id:
        if char in "/\\?#":
            return False
        if char.isspace() or ord(char) < 32 or ord(char) == 127:
            return False
    return True


def match_upstream_url(match_id: str) -> str:
    """拼出单场上游地址。match_id 只占一个已转义的路径段。"""
    return f"{UPSTREAM_ORIGIN}/matches/{quote(match_id, safe='')}"


def fetch_upstream_json(
    url: str,
    params: dict[str, str] | None,
    *,
    route_name: str,
    not_found_message: str,
) -> Any:
    """GET 本机上游并解析 JSON。失败时抛出 UpstreamFailure。"""
    timeout_seconds = upstream_timeout_seconds()
    max_bytes = upstream_max_bytes()
    try:
        raw = _read_upstream(
            url,
            params,
            timeout_seconds,
            max_bytes,
            route_name,
            not_found_message,
        )
    except UpstreamFailure:
        raise
    except _ResponseTooLarge:
        log.info("上游响应超过大小上限。路由 %s。", route_name)
        raise UpstreamFailure(
            502,
            "upstream_response_too_large",
            "上游响应超过大小上限。",
        ) from None
    except httpx.TimeoutException:
        log.info("上游超时。路由 %s。", route_name)
        raise UpstreamFailure(504, "upstream_timeout", "上游在限定时间内没有返回。") from None
    except httpx.RequestError as exc:
        log.info("上游连接失败。路由 %s，异常类型 %s。", route_name, type(exc).__name__)
        raise UpstreamFailure(502, "upstream_error", "上游暂时不可用。") from None
    return _decode_json(raw, route_name)


def shape_match_detail(match_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """保留编号、赛程摘要和赛果。不带回统计、赔率、元数据或预测字段。"""
    match = payload.get("match")
    result = payload.get("result")
    return {
        "id": match_id,
        "match": _shape_match(match) if isinstance(match, dict) else None,
        "result": _pick(result, _RESULT_FIELDS) if isinstance(result, dict) else None,
    }


def _shape_match(match: dict[str, Any]) -> dict[str, Any]:
    shaped = _pick(match, _MATCH_FIELDS)
    if "jc" in match:
        jc = match.get("jc")
        shaped["jc"] = _pick(jc, _JC_FIELDS) if isinstance(jc, dict) else None
    if "competition" in match:
        competition = match.get("competition")
        shaped["competition"] = (
            _pick(competition, _COMPETITION_FIELDS) if isinstance(competition, dict) else None
        )
    if "teams" in match:
        teams = match.get("teams")
        shaped["teams"] = _pick(teams, _TEAM_FIELDS) if isinstance(teams, dict) else None
    return shaped


def _pick(source: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: source[key] for key in fields if key in source}


def _read_upstream(
    url: str,
    params: dict[str, str] | None,
    timeout_seconds: float,
    max_bytes: int,
    route_name: str,
    not_found_message: str,
) -> bytes:
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    with open_client(timeout_seconds) as client:
        with client.stream("GET", url, params=params, headers=headers) as response:
            status = response.status_code
            if status == 404:
                raise UpstreamFailure(404, "not_found", not_found_message)
            if 400 <= status < 500:
                log.info("上游返回客户端错误。路由 %s，状态 %s。", route_name, status)
                raise UpstreamFailure(502, "upstream_client_error", "上游拒绝了该查询。")
            if status != 200:
                log.info("上游返回异常状态。路由 %s，状态 %s。", route_name, status)
                raise UpstreamFailure(502, "upstream_error", "上游暂时不可用。")
            return _read_capped(response, max_bytes)


def _declared_length(headers: object) -> int | None:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    raw = getter("content-length")
    if raw is None:
        return None
    text = str(raw).strip()
    if len(text) > 16 or not text.isdigit():
        return None
    return int(text)


def _read_capped(response: Any, max_bytes: int) -> bytes:
    declared = _declared_length(response.headers)
    if declared is not None and declared > max_bytes:
        raise _ResponseTooLarge()
    buf = bytearray()
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        if len(buf) + len(chunk) > max_bytes:
            raise _ResponseTooLarge()
        buf.extend(chunk)
    return bytes(buf)


def _decode_json(raw: bytes, route_name: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        log.info("上游响应无法解析为 JSON。路由 %s。", route_name)
        raise UpstreamFailure(502, "upstream_error", "上游响应无法解析。") from None
