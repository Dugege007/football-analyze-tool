# football-openclaw-gateway 阶段 B1 读适配

**状态**：2026-10-27 按契约草稿实现。开放问题采用本文第 2 节的默认决定。合入前由分析师／秘书安排测试。本文件不授权改动大陆站。  
**阶段 B2**：任务最小通道已经实现。当前的 `stage`、版本号、能力清单和 `POST /v1/jobs`、`GET /v1/jobs/{id}` 以 [`openclaw-gateway-b2.md`](openclaw-gateway-b2.md) 为准。本文件继续记录阶段 B1 的读适配决定。  
**代码**：`openclaw-gateway/`。不修改 `api/` 与 `web/` 的对外行为。  
**基线**：阶段 B0 骨架见 [`openclaw-gateway-b0.md`](openclaw-gateway-b0.md)。鉴权、监听地址和 Nginx 来源限制沿用该文件。  
**上游**：只读分析接口 `http://127.0.0.1:8787`。网关监听仍是 `127.0.0.1:8790`。

## 1. 本阶段交付

1. `GET /v1/health` 的 `stage` 为 `b1`，`version` 为 `0.1.0-b1`。
2. `GET /v1/capabilities` 的 `stage` 为 `b1`，并且只登记两条读能力：`read.matches`、`read.match`。
3. `GET /v1/read/matches` 把白名单查询转到本机 `GET /matches`，成功时透传上游 JSON。
4. `GET /v1/read/matches/{match_id}` 读取本机 `GET /matches/{match_id}` 后做精简封装。
5. 未登记的 `/v1/read/...` 仍先校验 Bearer，再返回 `501`。阶段 B1 交付时 `POST /v1/jobs` 与 `GET /v1/jobs/{id}` 仍返回 `501`；阶段 B2 起这两条改为真实任务通道。
6. 任何写方法都不进入上游白名单。

## 2. 开放问题的默认决定

契约草稿第 6 节的五个问题，本阶段按下面的默认处理。若要改口径，需要另开说明后再改代码。

1. **单场不补预测字段。** 不第二次请求 `/matches/{id}/predictions`。成功正文只有 `id`、`match`、`result`，不包含 `direction`、`picks`、`strategies`、`has_prediction`。这些字段仍只出现在列表透传结果里。
2. **列表整包透传，单场再裁。** 列表保留上游给出的别名数组和 pending 标记。单场 `match` 去掉别名数组、pending 类界面字段、扁平重复的常用名、内部编号和 `ids`。保留竞彩日、星期、范围、竞彩编号、赛事名与常用名、赛事类型与阶段、开赛小时与时刻、分钟是否已知、队名原文与常用名。
3. **`date` 仍然必填。** 网关不代填「今天」的竞彩日，也不实现切日规则。缺少、重复或不是有效 `YYYY-MM-DD` 时返回 `400`，`error.code` 为 `bad_request`，并且不访问上游。
4. **单场编号与上游一致。** `match_uid`（例如 `2026-10-09|五001`）和纯数字内部主键都可以。聊天侧应优先使用列表项里的 `id`。响应里的 `id` 就是调用方路径上的这个编号，网关不把它改写成另一种形式。
5. **上游 `404` 使用 `not_found`。** 不使用 `upstream_not_found`。单场的 `message` 为「比赛不存在。」列表的 `message` 说明没有找到对应的比赛列表。网关自己的未知路径仍是「请求的路径不存在。」调用方靠 `message` 区分这两类 `not_found`。

## 3. 探活与能力清单

`GET /v1/health` 默认仍不校验令牌。`GATEWAY_REQUIRE_AUTH_ON_HEALTH` 的规则与阶段 B0 相同。

```json
{
  "ok": true,
  "service": "football-openclaw-gateway",
  "version": "0.1.0-b1",
  "stage": "b1"
}
```

`GET /v1/capabilities` 需要 Bearer。成功正文固定为：

```json
{
  "stage": "b1",
  "capabilities": [
    {
      "name": "read.matches",
      "methods": ["GET /v1/read/matches"]
    },
    {
      "name": "read.match",
      "methods": ["GET /v1/read/matches/{match_id}"]
    }
  ]
}
```

公网形态仍是 Nginx 去掉 `/openclaw` 前缀之后的 `/v1/...`。来源地址限制不变。

## 4. 鉴权

与阶段 B0 相同。除健康检查按开关放行外，读路由先校验 Bearer。缺少令牌、格式错误或令牌不一致时返回 `401`，`error.code` 为 `unauthorized`。这种失败不会访问上游，也不会用 `501` 掩盖。

写到已登记读路径上的 `POST`、`PUT`、`PATCH`、`DELETE` 同样先校验令牌，通过后返回 `501`，不转发。未登记路径也是先鉴权再 `501`。

## 5. 上游白名单

| 项 | 约定 |
|---|---|
| 基址 | 固定 `http://127.0.0.1:8787`，不允许配置成其它主机或端口 |
| 方法 | 只发出 `GET` |
| 路径 | 只允许 `/matches` 与 `/matches/{match_id}`。`match_id` 是单段路径，不会拼上 `/odds` 或 `/predictions` |
| 查询串 | 列表只转发 `date` 和 `scope`。单场不转发任何查询键 |
| 请求头 | 固定发送 `Accept: application/json` 和 `User-Agent: football-openclaw-gateway/b1`。不转发调用方的 `Authorization` 及其它请求头 |
| 代理与重定向 | 不读取进程环境里的代理，不跟随上游重定向 |
| 超时 | `OPENCLAW_UPSTREAM_TIMEOUT_SECONDS`，默认 3 秒，上限 10 秒。连接、读取、写入和连接池等待都使用这个秒数 |
| 体积 | `OPENCLAW_UPSTREAM_MAX_BYTES`，默认 1048576，不允许提高 |

`scope` 只接受 `jingcai`、`extra`、`all`。不传时不代替上游填写，由上游按它自己的默认值处理。非法或重复的 `scope` 返回 `400` / `bad_request`，不访问上游。

路径段 `dates` 对应上游的日历接口，阶段 B1 不开放。`GET /v1/read/matches/dates` 在鉴权通过后返回 `501`。过长、含空白或含路径分隔符的编号直接返回 `404` / `not_found`，不访问上游。

## 6. 两条读接口

### 6.1 `GET /v1/read/matches`

查询参数 `date` 必填，`scope` 可选。成功时把上游 JSON 原样作为网关正文，不再包一层，也不删除列表项里的字段。

上游成功时的形态仍然是：

```json
{
  "date": "2026-10-27",
  "scope": "jingcai",
  "items": []
}
```

### 6.2 `GET /v1/read/matches/{match_id}`

成功正文：

```json
{
  "id": "2026-10-09|五001",
  "match": {
    "date": "2026-10-09",
    "weekday": "五",
    "scope": "jingcai",
    "jc": {"id": "五001", "weekday": "五", "no": "001"},
    "competition": {
      "name": "示例联赛",
      "canonical": "示例联赛",
      "type": "联赛",
      "stage": "常规"
    },
    "kickoff_hour": 19,
    "kickoff_at": "2026-10-09T19:35:00+08:00",
    "kickoff_minute_known": true,
    "teams": {
      "home": "主队原文",
      "away": "客队原文",
      "home_canonical": "主队常用名",
      "away_canonical": "客队常用名"
    }
  },
  "result": {
    "home_goals": 1,
    "away_goals": 0,
    "total_goals": 1,
    "wdl": "H"
  }
}
```

没有赛果时 `result` 为 `null`。上游缺失的可选字段不会被网关补造。`stats`、`odds`、`meta` 不会出现。

## 7. 错误映射

成功和失败响应都带 `Cache-Control: no-store`。失败正文仍是 `{"error": {"code": "...", "message": "..."}}`。网关不回显上游正文、堆栈、响应头或调用方令牌。

| 情况 | HTTP | `error.code` |
|---|---|---|
| 缺令牌、格式错、令牌不一致 | 401 | `unauthorized` |
| 查询参数不合法 | 400 | `bad_request` |
| 网关路径不存在 | 404 | `not_found`（message 为「请求的路径不存在。」） |
| 上游返回 404 | 404 | `not_found`（单场 message 为「比赛不存在。」） |
| 路径已注册但方法不对 | 405 | `method_not_allowed` |
| 已规划但未实现的读子路径或任务 | 501 | `not_implemented` |
| 上游其它 4xx | 502 | `upstream_client_error` |
| 上游 5xx、连接失败、重定向或无法解析的正文 | 502 | `upstream_error` |
| 上游响应超过大小上限 | 502 | `upstream_response_too_large` |
| 上游超时 | 504 | `upstream_timeout` |
| 网关未预期异常 | 500 | `internal_error` |

## 8. 本机冒烟

在 `openclaw-gateway/` 按该目录 `README.md` 启动。下面的期望不依赖公网。若本机没有 `8787`，带令牌访问两条读接口会得到 `502` / `upstream_error`，这不代表鉴权失败。单测用 httpx 替身，不需要真实的 `8787`。

1. `GET /v1/health` → `200`，`stage` 为 `b1`。
2. 无令牌 `GET /v1/capabilities` → `401`。
3. 带令牌 `GET /v1/capabilities` → `200`，含 `read.matches` 与 `read.match`，`stage` 为 `b1`。
4. 无令牌 `GET /v1/read/matches?date=2026-10-27` → `401`，响应里没有上游数据。
5. 带令牌访问同一列表，且 `8787` 可用 → `200`，正文含 `date` 与 `items`。
6. 带令牌访问已知 `match_uid` → `200`，有 `id`、`match`、`result`，没有 `stats`、`odds`、`meta`。
7. 带令牌访问未知编号 → `404`，`error.code` 为 `not_found`。
8. 带令牌访问 `GET /v1/read/matches/{id}/odds` 或其它未登记子路径 → `501`。
9. 带令牌 `POST /v1/jobs`。阶段 B1 交付时期望 `501`。阶段 B2 起，`{"type":"ping","payload":{}}` 期望 `202`，见 B2 文档。
10. 在 `openclaw-gateway/` 执行 `python -m pytest -q`。

不要在冒烟输出或聊天里打印 Bearer 令牌明文。

## 9. 明确不做

- 阶段 B1 不实现任务队列，`/v1/jobs` 在该阶段保持 `501`。阶段 B2 才开放 ping 任务，见 B2 文档。
- 不修改 `api/app/main.py` 的路由、鉴权和只读开关，也不给 `8787` 添加入站 Bearer。
- 不开放赔率时间线、数据表、策略、资金、调度或日历接口。
- 不把 `8787` 或 `8790` 放进公网或安全组。
- 本阶段不安装 systemd、不重载大陆站 Nginx、不发布到 `124.221.56.38`。仓库里的单元文件和 Nginx 片段仍只是示例。

## 10. 文档位置

| 内容 | 路径 |
|---|---|
| 本说明 | `docs/openclaw-gateway-b1.md` |
| 阶段 B2 任务通道 | `docs/openclaw-gateway-b2.md` |
| 阶段 B0 骨架 | `docs/openclaw-gateway-b0.md` |
| 本地运行说明 | `openclaw-gateway/README.md` |
| 转发与精简 | `openclaw-gateway/openclaw_gateway/upstream.py` |
| 单测 | `openclaw-gateway/tests/test_b1.py` |
