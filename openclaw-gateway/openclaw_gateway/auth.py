"""Bearer 令牌校验。比较使用摘要，避免因明文字符串长度不同而提前返回。"""

from __future__ import annotations

import hashlib
import hmac
import logging

from starlette.requests import Request

from openclaw_gateway.config import bearer_token

log = logging.getLogger("openclaw_gateway")


class Unauthorized(Exception):
    """缺少令牌、格式不对或令牌不一致。由应用统一渲染为 401。"""


def extract_bearer(authorization: str | None) -> str | None:
    """从 Authorization 头取出 Bearer 令牌。格式不对时返回 None。"""
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer":
        return None
    token = token.strip()
    if not token or " " in token:
        return None
    return token


def tokens_equal(presented: str, expected: str) -> bool:
    """判断调用方令牌与配置令牌是否相同。配置为空时一律视为不相同。"""
    if expected == "":
        return False
    presented_digest = hashlib.sha256(presented.encode("utf-8")).digest()
    expected_digest = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(presented_digest, expected_digest)


def ensure_authorized(authorization: str | None, request: Request) -> None:
    """校验 Bearer 令牌。失败时抛出 Unauthorized，且日志不记录令牌内容。"""
    presented = extract_bearer(authorization)
    if presented is None or not tokens_equal(presented, bearer_token()):
        log.info("鉴权未通过。方法 %s，路径 %s。", request.method, request.url.path)
        raise Unauthorized()
