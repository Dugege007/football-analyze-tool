"""读取网关进程的环境变量。

本模块只读进程环境，不读取仓库根目录的 `.env`，以免和现有分析接口的配置混在一起。
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("openclaw_gateway")

SERVICE_NAME = "football-openclaw-gateway"
VERSION = "0.1.0-b0"
STAGE = "b0"

# 现有网页数据接口占用 8787。网关不得抢这个端口。
_RESERVED_API_PORT = 8787
_DEFAULT_PORT = 8790
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_HEALTH_AUTH_OFF = frozenset({"0", "false", "no", "off"})
_HEALTH_AUTH_ON = frozenset({"1", "true", "yes", "on"})
_unknown_health_flags: set[str] = set()


def bearer_token() -> str:
    """返回已去掉首尾空白的入站令牌。未配置时返回空字符串。"""
    return os.environ.get("OPENCLAW_BEARER_TOKEN", "").strip()


def require_auth_on_health() -> bool:
    """健康检查是否也要校验 Bearer 令牌。

    未设置或取值为 0、false、no、off 时不校验。取值为 1、true、yes、on 时校验。
    无法识别的取值按要求校验处理，避免把拼写错误当成关闭鉴权。
    """
    raw = os.environ.get("GATEWAY_REQUIRE_AUTH_ON_HEALTH", "0").strip().lower()
    if raw in _HEALTH_AUTH_OFF:
        return False
    if raw in _HEALTH_AUTH_ON:
        return True
    if raw not in _unknown_health_flags:
        _unknown_health_flags.add(raw)
        log.warning(
            "GATEWAY_REQUIRE_AUTH_ON_HEALTH 的取值无法识别，健康检查改为要求 Bearer 令牌。"
        )
    return True


def gateway_bind() -> str:
    """返回规范化后的监听地址。未设置时默认为 127.0.0.1。"""
    if "GATEWAY_BIND" not in os.environ:
        host = "127.0.0.1"
    else:
        host = os.environ["GATEWAY_BIND"].strip()
        if not host:
            raise SystemExit(
                "GATEWAY_BIND 已设置但为空。只能填写本机回环地址 127.0.0.1、localhost 或 ::1。"
            )
    return assert_loopback_bind(host)


def assert_loopback_bind(host: str) -> str:
    """拒绝回环地址以外的监听地址，并返回规范化后的主机名。

    错误信息只说明允许的地址，不回显当前配置，避免把误填进该变量的令牌写进日志。
    """
    normalized = host.strip().lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized not in _LOOPBACK_HOSTS:
        raise SystemExit(
            "GATEWAY_BIND 只能是本机回环地址 127.0.0.1、localhost 或 ::1。"
            "当前配置已被拒绝，服务不会监听公网或局域网。"
        )
    return normalized


def gateway_port() -> int:
    """返回监听端口。未设置时默认为 8790，并拒绝占用 8787。

    错误信息只说明规则，不回显当前配置。
    """
    if "GATEWAY_PORT" not in os.environ:
        port = _DEFAULT_PORT
    else:
        raw = os.environ["GATEWAY_PORT"].strip()
        if not raw.isdigit():
            raise SystemExit(_port_error_message())
        port = int(raw)
    if port == _RESERVED_API_PORT or not 1 <= port <= 65535:
        raise SystemExit(_port_error_message())
    return port


def _port_error_message() -> str:
    return (
        "GATEWAY_PORT 必须是 1 到 65535 的整数，且不能是 8787。"
        "8787 保留给现有的 football-analyze-api。"
    )
