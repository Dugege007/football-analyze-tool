"""读取网关进程的环境变量。

本模块只读进程环境，不读取仓库根目录的 `.env`，以免和现有分析接口的配置混在一起。
"""

from __future__ import annotations

import logging
import math
import os

log = logging.getLogger("openclaw_gateway")

SERVICE_NAME = "football-openclaw-gateway"
VERSION = "0.1.0-b2"
STAGE = "b2"

# 任务库与执行时限。路径可被单测改到临时文件，生产默认不进 Git。
_DEFAULT_JOB_DB_PATH = "/opt/football-data/openclaw-jobs.sqlite3"
_DEFAULT_JOB_MAX_SECONDS = 10.0
_MAX_JOB_MAX_SECONDS = 30.0
_DEFAULT_JOB_CREATE_TIMEOUT_SECONDS = 5.0
_MAX_JOB_CREATE_TIMEOUT_SECONDS = 10.0
_DEFAULT_JOB_RETENTION_SECONDS = 24.0 * 60.0 * 60.0
_MAX_JOB_RETENTION_SECONDS = 7.0 * 24.0 * 60.0 * 60.0

# 只读分析接口的本机地址。阶段 B1 只允许这一处，不能改成其它主机或端口。
UPSTREAM_ORIGIN = "http://127.0.0.1:8787"
_DEFAULT_UPSTREAM_TIMEOUT_SECONDS = 3.0
_MAX_UPSTREAM_TIMEOUT_SECONDS = 10.0
_DEFAULT_UPSTREAM_MAX_BYTES = 1_048_576
_warned_config: set[str] = set()

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


def _warn_once(code: str, message: str) -> None:
    """同一类配置问题只警告一次。警告正文不包含环境变量的原值。"""
    if code in _warned_config:
        return
    _warned_config.add(code)
    log.warning("%s", message)


def upstream_timeout_seconds() -> float:
    """上游连接与读取超时（秒）。默认 3，超过 10 时按 10 执行。

    无法识别、非有限数或小于等于 0 时回到默认值。日志不回显原值。
    """
    raw = os.environ.get("OPENCLAW_UPSTREAM_TIMEOUT_SECONDS", "").strip()
    if raw == "":
        return _DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        _warn_once(
            "timeout-invalid",
            "OPENCLAW_UPSTREAM_TIMEOUT_SECONDS 无法解析，上游超时改用默认 3 秒。",
        )
        return _DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0:
        _warn_once(
            "timeout-invalid",
            "OPENCLAW_UPSTREAM_TIMEOUT_SECONDS 不是正的有限数，上游超时改用默认 3 秒。",
        )
        return _DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    if value > _MAX_UPSTREAM_TIMEOUT_SECONDS:
        _warn_once(
            "timeout-capped",
            "OPENCLAW_UPSTREAM_TIMEOUT_SECONDS 超过 10 秒，已按 10 秒执行。",
        )
        return _MAX_UPSTREAM_TIMEOUT_SECONDS
    return value


def job_db_path() -> str:
    """任务 SQLite 文件路径。未设置时使用分析机数据目录下的默认文件。

    单测应把 OPENCLAW_JOB_DB_PATH 指到临时文件。取值含空字符时忽略并回到默认路径。
    """
    raw = os.environ.get("OPENCLAW_JOB_DB_PATH", "").strip()
    if raw == "":
        return _DEFAULT_JOB_DB_PATH
    if "\x00" in raw:
        _warn_once(
            "job-db-path-invalid",
            "OPENCLAW_JOB_DB_PATH 无法使用，任务库改用默认路径。",
        )
        return _DEFAULT_JOB_DB_PATH
    return raw


def job_max_seconds() -> float:
    """单次任务最长执行时间（秒）。默认 10，超过 30 时按 30 执行。"""
    return _bounded_seconds(
        "OPENCLAW_JOB_MAX_SECONDS",
        _DEFAULT_JOB_MAX_SECONDS,
        _MAX_JOB_MAX_SECONDS,
        allow_zero=False,
        invalid_code="job-max-invalid",
        capped_code="job-max-capped",
        invalid_message="OPENCLAW_JOB_MAX_SECONDS 不是正的有限数，任务最长执行时间改用默认 10 秒。",
        capped_message="OPENCLAW_JOB_MAX_SECONDS 超过 30 秒，已按 30 秒执行。",
    )


def job_create_timeout_seconds() -> float:
    """创建请求本身的处理时限（秒）。默认 5，超过 10 时按 10 执行。"""
    return _bounded_seconds(
        "OPENCLAW_JOB_CREATE_TIMEOUT_SECONDS",
        _DEFAULT_JOB_CREATE_TIMEOUT_SECONDS,
        _MAX_JOB_CREATE_TIMEOUT_SECONDS,
        allow_zero=False,
        invalid_code="job-create-timeout-invalid",
        capped_code="job-create-timeout-capped",
        invalid_message="OPENCLAW_JOB_CREATE_TIMEOUT_SECONDS 不是正的有限数，创建超时改用默认 5 秒。",
        capped_message="OPENCLAW_JOB_CREATE_TIMEOUT_SECONDS 超过 10 秒，已按 10 秒执行。",
    )


def job_retention_seconds() -> float:
    """成功与失败任务的保留时间（秒）。默认 24 小时，超过 7 天时按 7 天执行。

    0 表示终态任务在下一次创建或查询时立即过期。负数或无法解析时回到 24 小时。
    """
    return _bounded_seconds(
        "OPENCLAW_JOB_RETENTION_SECONDS",
        _DEFAULT_JOB_RETENTION_SECONDS,
        _MAX_JOB_RETENTION_SECONDS,
        allow_zero=True,
        invalid_code="job-retention-invalid",
        capped_code="job-retention-capped",
        invalid_message="OPENCLAW_JOB_RETENTION_SECONDS 无法解析，任务保留时间改用默认 24 小时。",
        capped_message="OPENCLAW_JOB_RETENTION_SECONDS 超过 7 天，已按 7 天执行。",
    )


def _bounded_seconds(
    env_name: str,
    default: float,
    cap: float,
    *,
    allow_zero: bool,
    invalid_code: str,
    capped_code: str,
    invalid_message: str,
    capped_message: str,
) -> float:
    """读取秒数环境变量。日志不回显原值。"""
    raw = os.environ.get(env_name, "").strip()
    if raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        _warn_once(invalid_code, invalid_message)
        return default
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        _warn_once(invalid_code, invalid_message)
        return default
    if value > cap:
        _warn_once(capped_code, capped_message)
        return cap
    return value


def upstream_max_bytes() -> int:
    """单次上游响应体上限（字节）。默认 1 MiB，不允许提高。"""
    raw = os.environ.get("OPENCLAW_UPSTREAM_MAX_BYTES", "").strip()
    if raw == "":
        return _DEFAULT_UPSTREAM_MAX_BYTES
    if len(raw) > 16 or not raw.isdigit():
        _warn_once(
            "max-bytes-invalid",
            "OPENCLAW_UPSTREAM_MAX_BYTES 无法解析，响应体上限改用默认 1 MiB。",
        )
        return _DEFAULT_UPSTREAM_MAX_BYTES
    value = int(raw)
    if value < 1:
        _warn_once(
            "max-bytes-invalid",
            "OPENCLAW_UPSTREAM_MAX_BYTES 不是正整数，响应体上限改用默认 1 MiB。",
        )
        return _DEFAULT_UPSTREAM_MAX_BYTES
    if value > _DEFAULT_UPSTREAM_MAX_BYTES:
        _warn_once(
            "max-bytes-capped",
            "OPENCLAW_UPSTREAM_MAX_BYTES 超过 1 MiB，已按 1 MiB 执行。",
        )
        return _DEFAULT_UPSTREAM_MAX_BYTES
    return value
