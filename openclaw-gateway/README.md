# football-openclaw-gateway

本目录是独立的 OpenClaw 入站网关，当前阶段为 B2。它单独占用一个进程，做本机探活、Bearer 令牌校验、能力清单，把两条只读查询转到本机的分析接口，并提供只含 `ping` 的最小任务通道。它不并入现有的 `api/` 服务，也不修改 `api/app/main.py` 的路由、鉴权或只读逻辑。

当前行为以 [`docs/openclaw-gateway-b2.md`](../docs/openclaw-gateway-b2.md) 为准。阶段 B0 的骨架决定仍记在 [`docs/openclaw-gateway-b0.md`](../docs/openclaw-gateway-b0.md)，阶段 B1 的读适配仍记在 [`docs/openclaw-gateway-b1.md`](../docs/openclaw-gateway-b1.md)。下面说明如何在本机安装、启动和冒烟。大陆站的 systemd 与 Nginx 要等分析师／秘书测试通过之后再改，本目录里的单元文件和 Nginx 片段都只是示例。

阶段 B1 对读适配的开放问题采用这些默认：单场不另拉预测；列表整包透传，单场去掉别名数组和 pending 类界面字段；`date` 必填，不代填今天；`match_id` 接受 `match_uid` 或数字主键；上游 `404` 映射为 `not_found`。阶段 B2 只白名单任务类型 `ping`，幂等键使用请求体字段 `idempotency_key`，任务最长执行 10 秒，成功和失败都保留 24 小时。极短任务可以在创建请求内跑完，响应仍是任务快照，状态码为 `202`。

## 环境要求

需要 Python 3.12 或更高版本。网关只读取进程自己的环境变量，不会自动加载仓库根目录的 `.env`。令牌不要写进 Git。本目录的 `openclaw-gateway.env` 已被仓库的 `.gitignore` 排除，示例文件 `openclaw-gateway.env.example` 里的令牌留空。

| 变量 | 含义 | 默认 |
|---|---|---|
| `GATEWAY_BIND` | 监听地址。只接受 `127.0.0.1`、`localhost` 或 `::1` | `127.0.0.1` |
| `GATEWAY_PORT` | 监听端口。不允许使用 `8787` | `8790` |
| `OPENCLAW_BEARER_TOKEN` | 入站 Bearer 令牌。比较前会去掉首尾空白 | 空。空令牌无法通过鉴权 |
| `GATEWAY_REQUIRE_AUTH_ON_HEALTH` | 设为 `1`、`true`、`yes` 或 `on` 时，健康检查也要带令牌 | `0` |
| `OPENCLAW_UPSTREAM_TIMEOUT_SECONDS` | 访问本机 `8787` 的超时秒数，大于 `10` 时按 `10` 执行 | `3` |
| `OPENCLAW_UPSTREAM_MAX_BYTES` | 单次上游响应体上限。不允许高于 `1048576` | `1048576` |
| `OPENCLAW_JOB_DB_PATH` | 任务 SQLite 文件。单测使用临时路径 | `/opt/football-data/openclaw-jobs.sqlite3` |
| `OPENCLAW_JOB_MAX_SECONDS` | 单次任务最长执行秒数，大于 `30` 时按 `30` 执行 | `10` |
| `OPENCLAW_JOB_CREATE_TIMEOUT_SECONDS` | 创建请求处理超时秒数，大于 `10` 时按 `10` 执行 | `5` |
| `OPENCLAW_JOB_RETENTION_SECONDS` | 成功与失败任务的保留秒数。`0` 表示终态立即过期 | `86400` |

上游地址固定为 `http://127.0.0.1:8787`，不能改成其它主机。`0`、`false`、`no`、`off` 以及未设置，都表示健康检查不校验令牌。无法识别的取值会按要求校验处理。

## 安装依赖

在本目录创建虚拟环境并安装依赖：

```bash
cd openclaw-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活虚拟环境。

## 启动

下面的命令只监听本机 `127.0.0.1:8790`。令牌由当前 shell 临时生成，不会写入文件。

```bash
cd openclaw-gateway
source .venv/bin/activate
export OPENCLAW_BEARER_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export GATEWAY_BIND=127.0.0.1
export GATEWAY_PORT=8790
export GATEWAY_REQUIRE_AUTH_ON_HEALTH=0
python -m openclaw_gateway
```

`python -m openclaw_gateway` 会读取上述变量。如果地址不是回环地址，或者端口是 `8787`，进程会直接退出，不会开始监听。

本机也可以用 uvicorn 做同样的冒烟。uvicorn 命令行上的地址不会再经过上面的检查，所以必须显式写上本机地址和 `8790`，不要写成 `0.0.0.0`：

```bash
uvicorn openclaw_gateway.main:app --host 127.0.0.1 --port 8790
```

生产环境的示例单元使用 `python -m openclaw_gateway`，见 `systemd/football-openclaw-gateway.service`。

## 本机冒烟

保持服务运行，另开一个终端。读接口要得到业务数据，本机 `127.0.0.1:8787` 上需要已有只读分析接口。没有该进程时，带令牌访问下面最后两条会得到 `502`，`error.code` 为 `upstream_error`。

```bash
curl -sS -D - http://127.0.0.1:8790/v1/health
curl -sS -D - http://127.0.0.1:8790/v1/capabilities
curl -sS -D - -H "Authorization: Bearer $OPENCLAW_BEARER_TOKEN" http://127.0.0.1:8790/v1/capabilities
curl -sS -D - -X POST -H "Authorization: Bearer $OPENCLAW_BEARER_TOKEN" -H 'Content-Type: application/json' -d '{"type":"ping","payload":{"echo":"hello"}}' http://127.0.0.1:8790/v1/jobs
curl -sS -D - -H "Authorization: Bearer $OPENCLAW_BEARER_TOKEN" "http://127.0.0.1:8790/v1/read/matches?date=2026-10-27"
curl -sS -D - -H "Authorization: Bearer $OPENCLAW_BEARER_TOKEN" "http://127.0.0.1:8790/v1/read/matches/2026-10-27%7C%E4%B8%80001"
```

期望依次是：健康检查返回 `200`，`ok` 为 `true`，`service` 为 `football-openclaw-gateway`，`stage` 为 `b2`，`version` 为 `0.1.0-b2`；能力清单不带令牌时返回 `401`，`error.code` 为 `unauthorized`；带上正确令牌时返回 `200`，`capabilities` 含 `read.matches`、`read.match`、`jobs.create`、`jobs.get`；创建 `ping` 返回 `202`，正文是任务快照，`status` 为 `succeeded`，`result.ok` 为 `true`。列表在上游可用时返回 `200`，正文含 `date` 和 `items`。单场在上游有这场比赛时返回 `200`，正文有 `id`、`match`、`result`，没有 `stats`、`odds`、`meta`。不要把令牌打进日志或聊天。

查询上一步返回的任务编号：`GET /v1/jobs/{id}` 期望 `200`，终态为 `succeeded`。无令牌访问列表或创建任务都应是 `401`。带令牌访问 `/v1/read/matches/{id}/odds` 这类未登记子路径仍是 `501`。`GET /v1/jobs` 没有列表，期望 `405`。

## 运行单测

```bash
cd openclaw-gateway
source .venv/bin/activate
python -m pytest -q
```

单测使用进程内的临时令牌，并用 httpx 替身模拟上游，不连接公网，也不占用 `8790` 或 `8787`。任务库写在 pytest 的临时目录，不使用 `/opt/football-data/openclaw-jobs.sqlite3`。

## 部署示例（本拉取请求不执行）

- systemd 示例：`openclaw-gateway/systemd/football-openclaw-gateway.service`。运行用户写的是 `football`。环境变量文件示例对应服务器上的 `/opt/football-data/openclaw-gateway.env`，权限应为 `600`。
- Nginx 示例：`deploy/nginx/openclaw-gateway.conf`。公网前缀是 `/openclaw/`，只允许来源地址 `101.132.38.171`，反代到 `127.0.0.1:8790`。
- 把本目录的内容放到服务器的 `/opt/football-openclaw-gateway/` 之后，目录里应能直接看到 `openclaw_gateway` 包和 `requirements.txt`。虚拟环境放在该目录的 `.venv`。

这些文件进仓库只为了对照。分析师／秘书测试通过之前，不要安装单元、不要重载大陆站 Nginx，也不要把 `8787` 或 `8790` 加进安全组。
