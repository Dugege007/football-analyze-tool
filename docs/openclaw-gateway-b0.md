# football-openclaw-gateway 阶段 B0

**状态**：2026-10-27 已确认，按本文件实现。本文件只描述网关骨架，不授权改动大陆站。  
**阶段 B1**：同一进程已经接上读适配。当前的 `stage`、版本号、能力清单和两条只读路由以 [`openclaw-gateway-b1.md`](openclaw-gateway-b1.md) 为准。本文件继续记录阶段 B0 当时确认的骨架决定。任务接口仍然未实现。  
**代码**：`openclaw-gateway/`。本阶段不修改 `api/app/main.py`。  
**调用关系**：OpenClaw（阿里云出口地址 `101.132.38.171`）经过大陆站（宝塔，地址 `124.221.56.38`）访问分析机上的本网关。

总方案分五层。本文件覆盖前两层里已经可以落地的骨架，并写明后三层的字段形状。后三层在阶段 B0 不实现。

1. **通道**：由 Nginx 限制来源地址，使用现有的传输层安全或明文超文本传输，路径前缀为 `/openclaw/`。
2. **网关**：独立进程。负责令牌校验、健康检查、能力清单，以及尚未实现的扩展点。
3. **读接口**：以后由网关转发到现有的只读数据接口。本阶段不转发。
4. **任务**：以后异步入队并提供查询。本阶段只保留路径和字段形状。
5. **领域**：具体的采集与分析业务。本阶段不实现。

## 1. 目标与非目标

### 1.1 本阶段交付

- 独立进程，配有独立的 systemd 示例单元，不并入现有的、没有入站令牌的 `football-analyze-api`。
- 不对公网开放 `8787`。现有网页数据接口保持「只在本机监听，并由 Nginx 的 `/api/` 反向代理」这一现状。
- 将来的对外入口挂在大陆站 `124.221.56.38`。调用方来源地址只允许 OpenClaw 的出口 `101.132.38.171`，由 Nginx 强制执行。
- 实现 `GET /v1/health`。默认可以不带令牌，便于探活。是否改为要求令牌，由环境变量控制。
- 其余 `/v1/*` 中已经注册的接口校验 Bearer 令牌。
- 实现 `GET /v1/capabilities`。当前返回空列表，避免调用方误用尚未实现的能力。
- 文档和代码里保留读路由和任务路由。本阶段不实现业务读转发，不实现任务入队，也不执行任务。

### 1.2 本阶段明确不做

- 不改 `football-analyze-api` 的路由、鉴权方式和只读开关。
- 不把 `8787` 或 `8790` 开放到公网或安全组。
- 不实现真正的任务执行器，也不调度 `live_capture`、`result_ingest` 或建场等现网守护进程。
- 不把令牌写进仓库、网站根目录或聊天记录。

## 2. 已确认的决定

1. 健康检查默认不要求 Bearer 令牌。`GATEWAY_REQUIRE_AUTH_ON_HEALTH` 的默认值是 `0`。
2. 运行用户沿用现网的 `football`。权限收紧靠环境变量文件的权限 `600`，不在本阶段新建系统用户。
3. 监听端口是 `8790`，并且只绑定 `127.0.0.1`。启动器也接受 `localhost` 和 `::1`，拒绝其它地址。
4. 公网路径前缀是 `/openclaw/`，避免和现有的 `/api/` 混淆。
5. 已经规划但尚未实现的扩展点返回 `501`，`error.code` 为 `not_implemented`。
6. 阶段 B0 包含 `GET /v1/capabilities`。调用方必须带 Bearer 令牌，成功时 `capabilities` 为空数组。

## 3. 部署与进程

| 项 | 值 |
|---|---|
| 服务名 | `football-openclaw-gateway` |
| 仓库中的代码 | `openclaw-gateway/` |
| 服务器上的代码目录 | `/opt/football-openclaw-gateway` |
| 虚拟环境 | `/opt/football-openclaw-gateway/.venv` |
| 运行用户 | `football` |
| 监听 | `127.0.0.1:8790`。禁止绑定 `0.0.0.0`，禁止占用 `8787` |
| systemd 示例 | 仓库内 `openclaw-gateway/systemd/football-openclaw-gateway.service`，安装位置为 `/etc/systemd/system/football-openclaw-gateway.service` |
| 环境变量文件 | `/opt/football-data/openclaw-gateway.env`，权限 `600`，不进 Git |
| 日志 | 示例单元把标准输出交给 journald。若要文件日志，使用 `/var/log/football-analyze-tool/openclaw-gateway.log` |
| 公网前缀 | `https://124.221.56.38/openclaw/` 反向代理到本机 `8790`，并去掉 `/openclaw` 前缀 |

把仓库里的 `openclaw-gateway/` 目录内容放到 `/opt/football-openclaw-gateway/` 之后，该目录下应能直接看到 Python 包 `openclaw_gateway` 和 `requirements.txt`。

**环境变量：**

| 变量 | 含义 | 示例 |
|---|---|---|
| `GATEWAY_BIND` | 监听地址 | `127.0.0.1` |
| `GATEWAY_PORT` | 监听端口 | `8790` |
| `OPENCLAW_BEARER_TOKEN` | 入站 Bearer 令牌的明文，只存放在服务器上的环境变量文件中 | 由运维生成的随机长串 |
| `GATEWAY_REQUIRE_AUTH_ON_HEALTH` | `0` 或 `1`。默认 `0`，探活可以不带令牌 | `0` |

令牌至少使用 32 字节密码学随机数，编码成十六进制或 Base64 均可。只放在 `/opt/football-data/openclaw-gateway.env`，由 OpenClaw 一侧配置相同的值。禁止提交到 Git。网关进程不读取仓库根目录的 `.env`。

本拉取请求不在大陆站创建上述目录，不安装 systemd 单元，也不重载 Nginx。

## 4. Nginx 通道

在站点 `124.221.56.38` 的 server 块中新增独立的 `location`，不要改现有的 `location /api/`。仓库中的示例是 `deploy/nginx/openclaw-gateway.conf`：

```nginx
# OpenClaw 网关：只允许阿里云出口，反向代理到本机 8790
location /openclaw/ {
    allow 101.132.38.171;
    deny all;

    proxy_pass http://127.0.0.1:8790/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Authorization $http_authorization;
}
```

说明：

- 公网调用形态是 `http://124.221.56.38/openclaw/v1/health`。站点如果已经启用传输层安全，则使用 `https://`。
- `proxy_pass` 末尾的斜杠会去掉 `/openclaw` 前缀，网关看到的路径是 `/v1/...`。
- 不在白名单中的来源地址由 Nginx 直接拒绝，通常是 `403`，请求到不了网关进程。
- 确认并完成验收之前，不改现网 Nginx。真正落地时先备份虚拟主机配置，执行 `nginx -t` 通过后再重新加载。

防火墙和安全组继续只开放 `80`、`443`，以及运维所需的 `22`。不要开放 `8790` 或 `8787`。

## 5. 鉴权

### 5.1 规则

- 请求头的形式是 `Authorization: Bearer <token>`。方案名 `Bearer` 不区分大小写。
- 令牌与 `OPENCLAW_BEARER_TOKEN` 做恒定时间比较。比较的是二者的 SHA-256 摘要，避免明文长度不同时提前返回。比较前去掉首尾空白。
- 缺少请求头、格式错误或令牌不一致时返回 `401`，正文见错误码表。两种失败使用同一句说明，响应里不回显提交的令牌。
- 未配置令牌，或配置值去掉空白后为空时，没有任何字符串能通过校验。
- `GET /v1/health` 在 `GATEWAY_REQUIRE_AUTH_ON_HEALTH` 为关闭时不读取 `Authorization` 头。因此不能用健康检查来试探令牌是否正确。该变量为 `1` 时，健康检查使用与其它接口相同的校验。
- 本阶段不做多租户，不做刷新令牌，也不做双向传输层安全。这些都可以以后再加。

### 5.2 与现有 `/api/` 的关系

| 入口 | 鉴权 | 用途 |
|---|---|---|
| `/api/*` 到 `127.0.0.1:8787` | 没有入站 Bearer 令牌 | 现有网页界面的数据，大陆站保持只读 |
| `/openclaw/v1/*` 到 `127.0.0.1:8790` | Nginx 来源地址白名单，再加上 Bearer 令牌 | 只给 OpenClaw 使用的网关 |

两个进程、两套路径、两套鉴权完全分开。阶段 B0 的网关不会把 `/v1/read/*` 转发到 `8787`。阶段 B1 只转发白名单里的两条 GET，见 B1 文档。

## 6. 路由

统一错误正文的形状是：

```json
{
  "error": {
    "code": "string",
    "message": "string"
  }
}
```

### 6.1 `GET /v1/health`

**用途**：确认进程存活，并返回网关自己的版本。  
**鉴权**：默认不要求 Bearer 令牌。

**成功响应** `200`：

```json
{
  "ok": true,
  "service": "football-openclaw-gateway",
  "version": "0.1.0-b0",
  "stage": "b0"
}
```

| 字段 | 类型 | 含义 |
|---|---|---|
| `ok` | 布尔值 | 进程正常时为 `true` |
| `service` | 字符串 | 固定为 `football-openclaw-gateway` |
| `version` | 字符串 | 网关自身版本。阶段 B0 交付时为 `0.1.0-b0`；阶段 B1 起改为 `0.1.0-b1` |
| `stage` | 字符串 | 阶段标记。阶段 B0 交付时为 `b0`；阶段 B1 起为 `b1` |

### 6.2 `GET /v1/capabilities`

**用途**：声明当前网关已经开放的能力。阶段 B0 返回空列表，避免 OpenClaw 调用尚未实现的接口。  
**鉴权**：需要 Bearer 令牌。

**成功响应** `200`：

```json
{
  "stage": "b0",
  "capabilities": []
}
```

后续阶段的形状如下。本阶段不实现，只作为字段预留：

```json
{
  "stage": "b1",
  "capabilities": [
    {"name": "jobs", "methods": ["POST /v1/jobs", "GET /v1/jobs/{id}"]},
    {"name": "read.matches", "methods": ["GET /v1/read/matches"]}
  ]
}
```

### 6.3 预留的读路由

以后的统一前缀是 `/v1/read/...`。网关应先校验 Bearer 令牌，再转发到本机 `8787` 上的只读接口，并且做路径白名单，禁止转发写方法和敏感的管理路由。

阶段 B0 收到 `/v1/read` 或其任意子路径时，先校验令牌，然后返回 `501`。这样调用方可以区分「路径已经规划，但当前阶段没有启用」和「地址完全错误」。未带令牌或令牌错误时返回 `401`，不会返回 `501`。

阶段 B1 起，`GET /v1/read/matches` 与 `GET /v1/read/matches/{match_id}` 改为真实只读转发，不再返回 `501`。其余未登记的 `/v1/read/...` 仍然先鉴权再返回 `501`。`POST /v1/jobs` 与 `GET /v1/jobs/{id}` 也仍然是 `501`。上面能力清单预留示例里的 `jobs` 尚未开放。

### 6.4 预留的任务接口

#### `POST /v1/jobs`

**鉴权**：需要 Bearer 令牌。  
**本阶段行为**：校验通过后固定返回 `501`。不入队，不读取请求体，也不把请求体抄进响应。

以后的请求体建议使用这些字段，确认后仍可调整：

```json
{
  "type": "string",
  "payload": {},
  "idempotency_key": "string|null",
  "client_ref": "string|null"
}
```

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `type` | 字符串 | 是 | 任务类型。具体枚举由分析师和后端在后续阶段定稿 |
| `payload` | 对象 | 是 | 任务参数。未知字段是拒绝还是忽略，后续再定 |
| `idempotency_key` | 字符串或空值 | 否 | 幂等键。重复提交时返回同一个任务 |
| `client_ref` | 字符串或空值 | 否 | 调用方自己的引用号，原样回显 |

以后的成功响应是 `202`：

```json
{
  "id": "job_...",
  "type": "string",
  "status": "queued",
  "created_at": "2026-10-27T03:00:00+08:00",
  "client_ref": null
}
```

#### `GET /v1/jobs/{id}`

**鉴权**：需要 Bearer 令牌。  
**本阶段行为**：校验通过后固定返回 `501`。不查询任务是否存在。

以后的成功响应是 `200`：

```json
{
  "id": "job_...",
  "type": "string",
  "status": "queued|running|succeeded|failed|cancelled",
  "created_at": "2026-10-27T03:00:00+08:00",
  "updated_at": "2026-10-27T03:01:00+08:00",
  "result": null,
  "error": null,
  "client_ref": null
}
```

以后的状态只能按下面的方向变化。阶段 B0 仅在文档中保留，代码不执行状态迁移。

`queued` 可以变为 `running`。`running` 可以变为 `succeeded` 或 `failed`。尚未结束的任务可以变为 `cancelled`。

`GET /v1/jobs` 没有单独的列表接口。该路径已经由 `POST /v1/jobs` 占用，所以用 GET 或其它未登记的方法访问时返回 `405`。方法是否匹配由路由表先判断，因此这种 `405` 不要求先带令牌。其它尚未注册的路径返回 `404`。

## 7. 错误码

| HTTP 状态 | `error.code` | 何时出现 |
|---|---|---|
| 401 | `unauthorized` | 缺少令牌、格式错误或令牌不一致 |
| 403 | 通常由 Nginx 直接拒绝，没有本服务的 JSON 正文 | 来源地址不在白名单 |
| 404 | `not_found` | 未知路径，且不是预留的扩展点 |
| 405 | `method_not_allowed` | 路径存在，但方法不对 |
| 501 | `not_implemented` | 读路由或任务路由已规划，当前阶段未实现 |
| 500 | `internal_error` | 未预期的异常。日志记录类型和路径，正文不包含异常文本，也不包含密钥 |

阶段 B0 对 `POST /v1/jobs`、`GET /v1/jobs/{id}` 以及 `/v1/read/*` 统一返回 `501` 和 `not_implemented`。交互式文档路径 `/docs`、`/redoc`、`/openapi.json` 不提供，访问时按未知路径返回 `404`。

成功和失败的响应都带 `Cache-Control: no-store`，避免共享缓存保存探活或能力清单的结果。

## 8. 版本与兼容

- 统一资源定位符的主版本写在路径里，即 `/v1`。破坏性变更另开 `/v2`，不静默改变阶段 B0 已经承诺的字段含义。
- `stage` 随阶段递增，例如从 `b0` 到 `b1`。`capabilities` 同步增加条目。
- OpenClaw 一侧应以 `capabilities` 探测能力，不要写死尚未声明的路由。

## 9. 本机冒烟

这些步骤在开发机上执行，不接触大陆站。服务必须监听 `127.0.0.1`，不要监听 `0.0.0.0`。

1. 在 `openclaw-gateway/` 安装依赖并启动，命令见该目录的 `README.md`。
2. `curl -sS http://127.0.0.1:8790/v1/health`  
   阶段 B0 交付时期望 `stage` 为 `b0`。阶段 B1 起期望 `stage` 为 `b1`，见 B1 文档。
3. `curl -sS -o /tmp/cap.json -w '%{http_code}\n' http://127.0.0.1:8790/v1/capabilities`  
   期望：`401`，`error.code` 为 `unauthorized`。
4. `curl -sS -H "Authorization: Bearer $OPENCLAW_BEARER_TOKEN" http://127.0.0.1:8790/v1/capabilities`  
   阶段 B0 交付时期望 `capabilities` 为空数组。阶段 B1 起期望含 `read.matches` 与 `read.match`。
5. `curl -sS -X POST -H "Authorization: Bearer $OPENCLAW_BEARER_TOKEN" -H 'Content-Type: application/json' -d '{"type":"ping","payload":{}}' http://127.0.0.1:8790/v1/jobs`  
   期望：`501`，`error.code` 为 `not_implemented`。
6. 运行 `python -m pytest -q`。期望相关用例通过。

## 10. 大陆站冒烟（验收通过之后才允许执行）

下面的步骤必须在用户确认、并且分析师验收通过、完成部署之后执行。在此之前禁止修改大陆站的 Nginx、systemd 和安全组。

1. 在分析机上执行 `curl -sS http://127.0.0.1:8790/v1/health`。期望与本机冒烟相同。
2. 从任意网络执行 `curl -sS http://124.221.56.38/openclaw/v1/health`。Nginx 配置完成时期望相同。站点若已启用传输层安全，则改用 `https://`。
3. 不带令牌访问 `http://124.221.56.38/openclaw/v1/capabilities`。期望 `401`，`error.code` 为 `unauthorized`。
4. 带正确令牌访问同一路径。阶段 B0 交付时期望 `capabilities` 为空数组。阶段 B1 起期望含两条读能力，见 B1 文档。
5. 带正确令牌提交 `POST /openclaw/v1/jobs`，请求体为 `{"type":"ping","payload":{}}`。期望 `501`，`error.code` 为 `not_implemented`。
6. 从不属于 `101.132.38.171` 的机器访问 `/openclaw/v1/health`。期望 Nginx 返回 `403`，或由连接策略拒绝。请求不得进入网关的业务逻辑。
7. 回归现有网页接口：`curl -sS http://124.221.56.38/api/health`。期望仍是现有 `football-analyze-api` 的健康响应，行为与部署网关之前一致。
8. 从外网访问 `124.221.56.38:8790` 和 `124.221.56.38:8787`。期望超时或被拒绝。

## 11. 文档与示例的位置

| 内容 | 路径 |
|---|---|
| 本说明 | `docs/openclaw-gateway-b0.md` |
| 阶段 B1 读适配 | `docs/openclaw-gateway-b1.md` |
| 本地运行说明 | `openclaw-gateway/README.md` |
| 服务代码 | `openclaw-gateway/openclaw_gateway/` |
| 单测 | `openclaw-gateway/tests/test_b0.py`、`openclaw-gateway/tests/test_b1.py` |
| 依赖 | `openclaw-gateway/requirements.txt` |
| 环境变量示例 | `openclaw-gateway/openclaw-gateway.env.example` |
| systemd 示例 | `openclaw-gateway/systemd/football-openclaw-gateway.service` |
| Nginx 示例 | `deploy/nginx/openclaw-gateway.conf` |
