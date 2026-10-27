"""进程入口。读取监听地址与端口，拒绝非回环地址后再交给 uvicorn。"""

from __future__ import annotations

import logging

from openclaw_gateway.config import (
    STAGE,
    UPSTREAM_ORIGIN,
    VERSION,
    bearer_token,
    gateway_bind,
    gateway_port,
    require_auth_on_health,
)
from openclaw_gateway.main import app

log = logging.getLogger("openclaw_gateway")


def main() -> None:
    """按环境变量在本机回环地址上启动网关。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = gateway_bind()
    port = gateway_port()
    token = bearer_token()
    log.info(
        "football-openclaw-gateway 启动。版本 %s，阶段 %s，监听 %s:%s，"
        "健康检查鉴权 %s，令牌 %s，上游 %s。",
        VERSION,
        STAGE,
        host,
        port,
        "开启" if require_auth_on_health() else "关闭",
        "已配置" if token else "未配置",
        UPSTREAM_ORIGIN,
    )
    if not token:
        log.warning(
            "未配置 OPENCLAW_BEARER_TOKEN。除按配置放行的健康检查外，其余接口一律返回 401。"
        )
    elif len(token) < 32:
        log.warning(
            "OPENCLAW_BEARER_TOKEN 短于 32 个字符。生产环境请更换为至少 32 字节的密码学随机串。"
        )

    import uvicorn

    uvicorn.run(app, host=host, port=port, proxy_headers=False, log_level="info")


if __name__ == "__main__":
    main()
