# football-openclaw-gateway 阶段 B2 任务最小通道

**状态**：2026-10-27 按契约草稿实现。第 13 节默认项已由用户经秘书确认并锁定。其余开放问题采用本文第 3 节的建议默认。本文件不授权改动大陆站。  
**代码**：`openclaw-gateway/`。不修改 `api/` 与 `web/` 的对外行为，不改 Nginx 来源白名单，不轮换令牌。  
**基线**：阶段 B0 骨架见 [`openclaw-gateway-b0.md`](openclaw-gateway-b0.md)。阶段 B1 读适配见 [`openclaw-gateway-b1.md`](openclaw-gateway-b1.md)。鉴权、监听地址和两条只读路由沿用已落地行为。  
**任务库**：SQLite 文件，默认 `/opt/football-data/openclaw-jobs.sqlite3`。不写入比赛业务库。

## 1. 本阶段交付

1. `GET /v1/health` 的 `stage` 为 `b2`，`version` 为 `0.1.0-b2`。
2. `GET /v1/capabilities` 的 `stage` 为 `b2`。保留 `read.matches`、`read.match`，并登记 `jobs.create`、`jobs.get`。
3. `POST /v1/jobs` 只接受任务类型 `ping`。创建成功返回 `202` 和任务快照。
4. `GET /v1/jobs/{id}` 返回同一快照。未知或已过期的编号返回 `404`。
5. 未登记的 `/v1/read/...` 仍先校验 Bearer，再返回 `501`。`GET /v1/jobs` 仍返回 `405`。
6. 不提供取消接口，也不提供第二个任务类型。

## 2. 已锁定的默认

| 项 | 锁定值 |
|---|---|
| 白名单 `type` | 只有 `ping`。其它类型返回 `400` / `bad_request`，不入队 |
| 幂等键 | 只认请求体字段 `idempotency_key`。不认请求头 |
| 任务最长执行时间 | 10 秒。超时后任务为 `failed`，`error.code` 为 `job_timeout` |
| 任务库 | SQLite，默认 `/opt/football-data/openclaw-jobs.sqlite3`。环境变量 `OPENCLAW_JOB_DB_PATH` 可改路径；单测使用临时文件 |
| `stage` / `version` | `b2` / `0.1.0-b2` |
| 能力名 | `jobs.create` 与 `jobs.get`。保留两条读能力 |
| 终态保留 | 成功与失败都是 24 小时。过期后查询按不存在处理 |
| 极短任务 | 可以在创建请求内跑完。响应仍是任务快照，状态码仍是 `202` |

## 3. 其它开放问题的建议默认

契约草稿第 12 节里尚未单独点名、但需要落地的条目，按下面的建议默认处理。

1. **幂等规范化不含 `client_ref`。** 相同幂等键是否命中，只比较 `type` 与 `payload`。重试可以更换 `client_ref`，仍返回第一次写入的快照，不改已保存的引用号。
2. **已清理任务返回 `404` / `not_found`。** `message` 为「任务不存在。」不使用 `410`。
3. **创建请求处理超时返回 `504` / `timeout`。** 默认 5 秒，环境变量 `OPENCLAW_JOB_CREATE_TIMEOUT_SECONDS`。这与任务执行超时不是同一件事。
4. **不实现 `cancelled`。** 状态字面量只留在本文说明里。代码不写入该状态，也没有取消路径。
5. **大陆站部署另要授权。** 本阶段只合代码、文档和单测。不安装 systemd，不重载 Nginx，不发布到 `124.221.56.38`。

## 4. 探活与能力清单

`GET /v1/health` 默认仍不校验令牌。`GATEWAY_REQUIRE_AUTH_ON_HEALTH` 的规则与阶段 B0 相同。

```json
{
  "ok": true,
  "service": "football-openclaw-gateway",
  "version": "0.1.0-b2",
  "stage": "b2"
}
```

`GET /v1/capabilities` 需要 Bearer。成功正文固定为：

```json
{
  "stage": "b2",
  "capabilities": [
    {"name": "read.matches", "methods": ["GET /v1/read/matches"]},
    {"name": "read.match", "methods": ["GET /v1/read/matches/{match_id}"]},
    {"name": "jobs.create", "methods": ["POST /v1/jobs"]},
    {"name": "jobs.get", "methods": ["GET /v1/jobs/{id}"]}
  ]
}
```

读路径的上游 `User-Agent` 仍是 `football-openclaw-gateway/b1`。阶段 B2 不改这条只读请求的形状。

## 5. 鉴权

与阶段 B0、B1 相同。`POST /v1/jobs` 和 `GET /v1/jobs/{id}` 必须先通过 Bearer。缺少令牌、格式错误或令牌不一致时返回 `401` / `unauthorized`，不读业务正文、不入队，也不用 `501` 掩盖。

`GET /v1/jobs`（没有编号）仍由路由表返回 `405` / `method_not_allowed`。这种 `405` 不要求先带令牌。

## 6. `POST /v1/jobs`

公网形态是 `POST /openclaw/v1/jobs`。Nginx 去掉 `/openclaw` 前缀后，网关看到 `/v1/jobs`。

请求体：

```json
{
  "type": "ping",
  "payload": {"echo": "hello"},
  "idempotency_key": "oc-20261027-001",
  "client_ref": "qq-msg-123"
}
```

| 字段 | 规则 |
|---|---|
| `type` | 必填。只允许字符串 `ping`。缺失、非字符串或其它值都是 `400`，说明为「不支持的任务类型。」响应不回显提交的类型 |
| `payload` | 必填，且必须是对象。`ping` 允许 `{}`。可选字符串 `echo`，最长 128 个字符。未知键拒绝 |
| `idempotency_key` | 可选。空字符串或 JSON `null` 视为未提供。非空时须为不超过 128 个字符的可见 ASCII（不含空格与控制符） |
| `client_ref` | 可选。空字符串会原样保存。JSON `null` 视为未提供。最长 256 个字符，不允许控制符 |

请求体还必须是对象，且不能出现上表以外的字段。无法解析、空正文或超过 64 KiB 时返回 `400` / `bad_request`。错误说明不包含字段值。

`ping` 不访问 `8787`，不读比赛库，不调度现网守护进程。网关在创建请求内把任务写成 `queued`，再写成 `running`，然后写入终态。创建响应只带终态快照，状态码是 `202`。存储里的 `entered_running` 记为 1，用来表明曾经进入过 `running`。

成功且已经跑完时：

```json
{
  "id": "job_0123456789abcdef0123456789abcdef",
  "type": "ping",
  "status": "succeeded",
  "created_at": "2026-10-27T14:30:00+08:00",
  "updated_at": "2026-10-27T14:30:00+08:00",
  "result": {
    "ok": true,
    "echo": "hello",
    "server_time": "2026-10-27T14:30:00+08:00"
  },
  "error": null,
  "client_ref": "qq-msg-123",
  "idempotency_key": "oc-20261027-001"
}
```

| 字段 | 含义 |
|---|---|
| `id` | `job_` 加 32 位十六进制。不使用可猜测的连续整数 |
| `created_at` / `updated_at` / `server_time` | 北京时间偏移的 ISO 8601，精度到秒 |
| `result.ok` | 成功时固定为 `true` |
| `result.echo` | 请求带了 `payload.echo` 时原样回显。未提供时为 JSON `null`，不省略该字段 |
| `result.server_time` | 与进入成功终态时的 `updated_at` 相同 |
| `error` | 成功时为 `null`。失败时为 `{"code","message"}`，且 `result` 为 `null` |

任务执行超过 `OPENCLAW_JOB_MAX_SECONDS` 时，创建响应仍是 `202`，`status` 为 `failed`，`error.code` 为 `job_timeout`。执行中出现未预期异常时，`error.code` 为 `job_internal_error`，说明不包含异常文本。

创建请求本身超过 `OPENCLAW_JOB_CREATE_TIMEOUT_SECONDS` 且没能返回快照时，响应是 `504` / `timeout`。若任务其实已经跑完，记录仍留在库里。带同一幂等键和同一 `type`、`payload` 再提交一次，会拿到该快照，状态码为 `202`。

## 7. `GET /v1/jobs/{id}`

需要 Bearer。成功时 `200`，正文与创建快照相同。任务处于 `failed` 时也是 `200`，失败细节在正文的 `error` 字段，避免和「查询路径错误」混用状态码。

编号不存在、不属于本网关，或终态记录已超过保留时间：`404` / `not_found`，`message` 为「任务不存在。」

## 8. 状态机

对外可见的创建结果是终态。存储层的顺序是：

```
queued → running → succeeded
queued → running → failed
```

终态不再迁出。阶段 B2 不写入 `cancelled`。

调用方若看到终态，不必再轮询。若将来出现未结束状态，建议先每 1 秒查询一次，连续 10 次仍未结束再改为每 3 秒，并在最长执行时间的 2 倍之内放弃。

## 9. 幂等

作用域是当前这一个共享 Bearer 身份，在保留期内：

| 情况 | 行为 |
|---|---|
| 未提供幂等键 | 每次创建都是新任务 |
| 相同幂等键，且 `type` 与 `payload` 相同 | `202`，返回同一个 `id` 和当前快照，不新插入一行，也不再次执行 |
| 相同幂等键，但 `type` 或 `payload` 不同 | `409` / `idempotency_conflict`，不覆盖原任务 |
| 原记录已过保留期 | 该键可以重新使用 |

`client_ref` 不参与比较。

## 10. 超时、保留与错误码

| 变量 | 默认 | 上限 |
|---|---|---|
| `OPENCLAW_JOB_MAX_SECONDS` | 10 秒 | 大于 30 时按 30 秒 |
| `OPENCLAW_JOB_CREATE_TIMEOUT_SECONDS` | 5 秒 | 大于 10 时按 10 秒 |
| `OPENCLAW_JOB_RETENTION_SECONDS` | 86400 秒 | 大于 7 天时按 7 天。`0` 表示终态在下一次创建或查询时立即过期 |

无法解析、负数，或（除保留时间外）小于等于 0 的取值回到默认。日志不回显原值。

成功和失败响应都带 `Cache-Control: no-store`。失败正文仍是：

```json
{"error": {"code": "string", "message": "string"}}
```

| HTTP | `error.code` | 何时 |
|---|---|---|
| 401 | `unauthorized` | 缺令牌、格式错、令牌不一致 |
| 400 | `bad_request` | 正文无效、缺字段、不支持的 `type`、`payload` 不合法、幂等键非法 |
| 404 | `not_found` | 任务不存在或已清理。网关未知路径的说明仍是「请求的路径不存在。」 |
| 405 | `method_not_allowed` | 例如 `GET /v1/jobs` |
| 409 | `idempotency_conflict` | 同幂等键、不同的 `type` 或 `payload` |
| 501 | `not_implemented` | 仍未开放的读子路径。已实现的 ping 创建和查询不再返回此码 |
| 504 | `timeout` | 创建请求处理超时 |
| 500 | `internal_error` | 未预期异常或任务库不可用。正文不包含密钥、堆栈和文件路径 |

查询一个已失败的任务时，HTTP 仍是 `200`。此时正文里的 `error.code` 可能是 `job_timeout` 或 `job_internal_error`。

## 11. 存储

表 `jobs` 至少包含编号、类型、状态、创建与更新时间、完成时间、结果、错误、`client_ref`、幂等键、规范化正文，以及是否进入过 `running`。幂等键在非空时唯一。

文件权限尽量设为 `600`。进程只为这一份任务库打开 SQLite，不把任务行写进比赛库。默认路径的父目录在分析机上应已存在。单测通过 `OPENCLAW_JOB_DB_PATH` 指向临时文件。

过期清理在创建和查询时进行，只删除已经进入 `succeeded` 或 `failed` 且完成时间早于保留窗口的行。不删除仍处于 `queued` 或 `running` 的行。

## 12. 本机冒烟

在 `openclaw-gateway/` 按该目录 `README.md` 启动。下面的期望不依赖公网，也不打印令牌明文。单测不需要真实的 `8787`。

1. `GET /v1/health` → `200`，`stage` 为 `b2`，`version` 为 `0.1.0-b2`。
2. 无令牌 `POST /v1/jobs` → `401` / `unauthorized`。
3. 带令牌 `GET /v1/capabilities` → `200`，含 `jobs.create`、`jobs.get`，并仍含 `read.matches`、`read.match`。
4. 带令牌 `POST /v1/jobs`，正文 `{"type":"ping","payload":{}}` → `202`，有 `id`，`status` 为 `succeeded`，`result.ok` 为 `true`，`result.echo` 为 `null`。
5. 带令牌 `GET /v1/jobs/{上一步 id}` → `200`，`status` 为 `succeeded`，`error` 为 `null`。
6. 相同 `idempotency_key` 与相同 `type`、`payload` 再创建 → 同一个 `id`。
7. 相同 `idempotency_key`、不同 `payload` → `409` / `idempotency_conflict`。
8. `type` 为 `not_a_real_type` → `400` / `bad_request`，不入队。
9. `GET /v1/jobs/job_does_not_exist` → `404` / `not_found`，说明为「任务不存在。」
10. 带令牌 `GET /v1/read/matches?date=…` 仍是阶段 B1 行为。
11. 在 `openclaw-gateway/` 执行 `python -m pytest -q`。

## 13. 明确不做

- 不实现 `ping` 以外的任务类型，不调度 `live_capture`、`result_ingest` 或建场。
- 不提供 `GET /v1/jobs` 列表，不提供取消接口。
- 不修改 `api/app/main.py`，不给 `8787` 添加入站 Bearer。
- 不改 Nginx 来源白名单，不把 `8787` 或 `8790` 放进公网或安全组。
- 不在仓库、文档或拉取请求正文中写入令牌明文。
- 本阶段不安装 systemd，不重载大陆站 Nginx。仓库里的单元文件和 Nginx 片段仍只是示例。

## 14. 文档位置

| 内容 | 路径 |
|---|---|
| 本说明 | `docs/openclaw-gateway-b2.md` |
| 阶段 B0 骨架 | `docs/openclaw-gateway-b0.md` |
| 阶段 B1 读适配 | `docs/openclaw-gateway-b1.md` |
| 任务存储与 ping | `openclaw-gateway/openclaw_gateway/jobs.py` |
| 单测 | `openclaw-gateway/tests/test_b2.py` |
