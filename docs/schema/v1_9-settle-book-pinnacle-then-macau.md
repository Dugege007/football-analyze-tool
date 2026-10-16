# v1.9 · 结算盘默认平博临盘优先（2026-10-16）

预测、回测、切片在**没有写明结算盘**时，改为平博临盘优先，没有平博临盘再用澳门临盘。

水位版本不动：默认仍是 `ah_v4_water_midpoint`（澳门固定 0.95；平博用库内真实水位）。

## 口径

`settle_book_priority = pinnacle_close > macau_close`

| 请求或行内 `settle_book` | 行为 |
|---|---|
| 缺省、空、`pinnacle_then_macau`、`pinnacle_close>macau_close` | 该场有平博 close 亚盘（含平手 0）→ 用平博；没有则用澳门 close |
| 显式 `macau_close` | 只走澳门，即使平博有盘也不用 |
| 显式 `pinnacle_close` 或其他单一 `book_phase` | 只走该庄，不回退 |

有临盘指 `odds_asian.handicap` 非空。平博行在但盘口为空，视作没有平博临盘。

## 不改的部分

- 不批量改写 `predictions.settle_book`。CFFXDJ_5_V3 等已写 `macau_close` 的冻结行保持原值。
- 数据表逐场结算读行内 `settle_book`。写死澳门的场仍只按澳门结算。
- `market_settle` 五庄（及多家平均）各自结算，缺临盘不借另一家。
- 不改澳门固定 0.95 的水位版本，不打开双写，不改正式库数据。

要复现旧回测：请求里显式传 `"settle_book": "macau_close"`。

## 版本

结算盘版本与水位 `settlement_version` 分开。

| 常量 | 值 |
|---|---|
| `DEFAULT_SETTLE_BOOK` | `pinnacle_then_macau` |
| `SETTLE_BOOK_PRIORITY` | `pinnacle_close`，然后 `macau_close` |
| `SETTLEMENT_BOOK_VERSION` | `settle_book_pinnacle_then_macau_v1` |

优先级回测把 `settlement_book_version` 与 `settle_book_priority` 写入 run 指纹。显式 `macau_close` 的指纹与改前相同，旧 run 仍可复用。

## 给前端的字段

HelpTip 可按这些字段写文案。本仓不改 `web/`。

### 请求缺省

`POST /strategies/{id}/validate` 与 `POST /strategies/compare` 的 `settle_book` 缺省从 `macau_close` 改为 `pinnacle_then_macau`。

新策略模板 `config.settle_book` 同样缺省为 `pinnacle_then_macau`。已入库且写了 `macau_close` 的方案配置不改。

### 响应

| 字段 | 出现位置 | 含义 |
|---|---|---|
| `settle_book` | validate / compare 顶层；摘要；数据表 `settlement` | 本次请求或预测行上的结算盘令牌。缺省为 `pinnacle_then_macau`。冻结行仍是 `macau_close` |
| `settle_book_priority` | validate / compare 顶层；摘要 | 优先级生效时为 `["pinnacle_close", "macau_close"]`；显式单庄时为 `null` |
| `settlement_book_version` | 同上 | 优先级生效时为 `settle_book_pinnacle_then_macau_v1`；显式单庄时为 `null` |
| `settled_with` | 数据表 `settlement`；回测 cache 行及其 `metrics`；cache 列表项顶层 | 这场实际用的庄：`pinnacle` 或 `macau`。未结算时 `null` |
| `fallback_book` | 同上 | 发生回退时为 `macau`；未回退为 `null`（可省略，当前接口写出 `null`） |
| `settled_with_counts` | validate 摘要、compare stack 摘要 | 按实际庄计数，例如 `{"pinnacle": 10, "macau": 2}`。没有可计场次时为 `null` |
| `n_settle_book_fallback` | 同上 | `fallback_book` 非空的场次数。与水位字段 `n_fallback_095` 不是一回事 |

水位说明仍看 `juice_note`。优先级下：平博用库内水位；退回澳门时 `juice_source=fixed_macau`（0.95）。

### 数据表

`GET /table/matches` 的每场 `settlement` 增加 `settled_with`、`fallback_book`。

冻结预测行 `settle_book=macau_close` 时：`settled_with=macau`，`fallback_book=null`，盘口仍是澳门临盘。

行内 `settle_book` 为空时才走平博优先。
