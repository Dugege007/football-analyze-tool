# 日历置灰 · `GET /matches/dates`

只读。给赛程日历标出「这一段竞彩日里，哪些天至少有一场比赛」。没有比赛行的日期不出现在结果里，前端据此置灰。

不改表结构。不读赔率、预测或赛果：库里有一条 `matches` 行即算有赛程。

## 请求

`GET /matches/dates?from=YYYY-MM-DD&to=YYYY-MM-DD&scope=`

| 参数 | 必填 | 说明 |
|---|---|---|
| `from` | 是 | 竞彩日起，闭区间，`YYYY-MM-DD`。与赛程页 `jingcai_date`、`GET /matches?date=` 同一列 |
| `to` | 是 | 竞彩日止，闭区间，`YYYY-MM-DD` |
| `scope` | 否 | 默认 `jingcai`。与 `GET /matches` 的 `matches.scope` 过滤对齐：`jingcai` → `scope='jingcai'`；`extra` 与别名 `extended` → `scope='extra'`；`all` → 不加 scope 条件 |

`from` 或 `to` 不是合法日期（含 `2026-02-31` 这类日历上不存在的日子）时返回 **400**，`detail` 含 `YYYY-MM-DD`。`from > to` 返回 **400**，`detail` 为 `from > to`。缺参按 FastAPI 校验返回 **422**。`scope` 不是 `jingcai|extra|extended|all` 时返回 **422**。

## 响应

```json
{ "dates": ["YYYY-MM-DD"] }
```

`dates` 是区间内去重后的 `jingcai_date`，按日期升序。区间内没有任何比赛行时为 `[]`，仍是 200。

## 示例

下面的 JSON 是契约示意，不是现网某一天的实数。请求可以直接照抄，把日期换成要查的竞彩日。示意库里的比赛行是：

- `jingcai`：2026-06-01、06-02、06-04、06-05
- `extra`：2026-06-03、06-04、06-06

有赛程的竞彩日（默认只看竞彩场）：

```bash
curl -s 'http://127.0.0.1:8787/matches/dates?from=2026-06-01&to=2026-06-07'
```

```json
{ "dates": ["2026-06-01", "2026-06-02", "2026-06-04", "2026-06-05"] }
```

非竞彩场。`extended` 与 `extra` 同义：

```bash
curl -s 'http://127.0.0.1:8787/matches/dates?from=2026-06-01&to=2026-06-07&scope=extended'
```

```json
{ "dates": ["2026-06-03", "2026-06-04", "2026-06-06"] }
```

竞彩与非竞彩一起看：

```bash
curl -s 'http://127.0.0.1:8787/matches/dates?from=2026-06-02&to=2026-06-02&scope=all'
```

```json
{ "dates": ["2026-06-02"] }
```

这段里一场都没有：

```bash
curl -s 'http://127.0.0.1:8787/matches/dates?from=2026-07-01&to=2026-07-03&scope=all'
```

```json
{ "dates": [] }
```

起止颠倒：

```bash
curl -s 'http://127.0.0.1:8787/matches/dates?from=2026-06-05&to=2026-06-02'
```

```json
{ "detail": "from > to" }
```

路径固定为 `/matches/dates`，注册在 `/matches/{match_id}` 之前，`dates` 不会被当成比赛编号。
