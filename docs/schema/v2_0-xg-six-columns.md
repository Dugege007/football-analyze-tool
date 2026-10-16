# 数据表预期进球六列（2026-10-16）

预期进球属于基本面，与机构无关。不自建射门模型，也不按机构拆列，也不做多家平均。主源是 BSD（bzzoiro）的赛事统计 `GET /api/v2/events/{id}/stats/`。射门明细和逐分钟曲线不进入宽表。

库和接口保留供应商给出的完整精度。保留两位小数只在前端展示时做。后端不把数值四舍五入到两位小数。

## 字段契约

嵌套响应在 `GET /table/matches` 每一行的 `fundamentals` 下面。六个键各自一个外壳，不共用同一个状态：

| 键 | 含义 |
|---|---|
| `xg_ft_home` | 全场主队预期进球 |
| `xg_ft_away` | 全场客队预期进球 |
| `xg_ht_home` | 上半场主队预期进球 |
| `xg_ht_away` | 上半场客队预期进球 |
| `xg_2h_home` | 下半场主队预期进球 |
| `xg_2h_away` | 下半场客队预期进球 |

每个外壳包含 `value`（数字或空）、`status`、`source`、`as_of`、`reason`、`reason_zh`，以及可选的 `quality`（`measured`、`estimated` 或 `derived`）。

`value` 是完整精度的数字。扁平导出里，这六个键本身就是数值列，状态、来源、时刻、原因和质量使用同名加后缀的列，例如 `xg_ft_home_status`。

状态：

- 未完赛：`not_filled`。页面显示「-」。即使库里已经有赛后观测，接口在赛果可见之前也不返回数值。
- 映射表已经建好，但这场没有 BSD 赛事编号：`unavailable`，`reason` 为 `no_bsd_mapping`。
- BSD 没有这场的预期进球：`unavailable`，`reason` 为 `no_bsd_coverage`。
- 只有一侧的上半场缺失：这一列 `unavailable`，其余有值的列仍是 `ok`。
- 估算值：`quality` 为 `estimated`，数值照常给出。页面用灰色字，悬停「估算」。

下半场：

- 优先使用 BSD 独立给出的 `second_half`。
- 没有独立下半场、但这一侧的全场和上半场都有数值时，写入前用全场减去上半场。`quality` 为 `derived`，`reason_zh` 为「由全场减上半场得出」。减法保持完整精度。
- 全场或上半场这一侧缺失时，对应的下半场是 `unavailable`，数值为空，不用零顶上。
- 接口只读已经写入的下半场，前端也不再做减法。

落库是 `stats_obs` 的一行，`kind` 为 `xg`。载荷包含全场、上半场、下半场的主客数值，以及 `quality`、`quality_detail`、`quality_by_side`、`h2_origin`、`source`、`provider_event_id`、`scope`（完赛后为 `postmatch_actual`）和 `as_of`。宽表快捷列这一期不做。

## 映射

`bsd_event_map` 保存本库比赛编号和 BSD 赛事编号。历史回填不会猜测队名。没有对应行的完赛场次记为无法获取。

## 队列

队列表是 `xg_fetch_queue`，和 5DollarFootballAPI 的盘口队列不是同一套。免费额度每天 7500 次，按协调世界时的日期记在单独的账本里（默认 `data/odds-data/backfill/xg-bsd-queue/quota.json`）。达到上限后停止请求，不把计数再加一。

历史脚本默认干跑：

```bash
python scripts/backfill/xg_bsd_history_queue.py --db /path/to/app.db
```

干跑只打印完赛场数和无法获取的原因分布，不建表，不发请求，不写观测。确认之后再拉取，例如一次 20 场、间隔 2 秒：

```bash
python scripts/backfill/xg_bsd_history_queue.py --db /path/to/app.db --apply --limit 20 --min-interval 2
```

必须显式传入 `--db`。写正式库之前要先备份，并设置 `FORMAL_DB_BACKUP_DONE=1`。合并之前不要对正式库加 `--apply`。令牌用环境变量 `BSD_BZZOIRO_API_KEY`，请求头是 `Authorization: Token <令牌>`。没有令牌时脚本退出且不写观测。

补表同样默认不写库：

```bash
python api/scripts/ensure_xg_obs_schema.py --db /path/to/app.db
python api/scripts/ensure_xg_obs_schema.py --db /path/to/app.db --apply
```

## 日采钩子

不另起守护进程。赛果入库 `scripts/live/result_ingest.py` 的 `apply` 在新完赛比分写入的同一事务里入队，此时不请求 BSD。同一文件的 `tick` 在本轮赛果处理之后调用 `pull_xg_after_tick`：有令牌就拉取已到期的场次，没有令牌就保持未填写。第一次拉取成功后，如果还没满两次，下一次到期时间是三小时之后，仍由后来的 `tick` 或历史脚本带走，用来补上赛后统计延迟。

## 页面

六个列只出现在数据表的「预测」标签，放在基本面分组里。机构标签不加这六列。有值时展示两位小数；未填写或缺少键时显示「-」；无法获取时显示「无法获取」。

## 验收对照

1. 预测标签能看到六列；机构标签没有这六列。
2. 有 BSD 预期进球的完赛场次，六列数值与 BSD 一致。允许展示成两位小数，库和接口仍是完整精度。
3. 没有覆盖的场次显示「无法获取」。
4. 未完赛显示「-」。
5. 历史回填的干跑报告给出覆盖场数和无法获取的原因分布。独立下半场、由全场减上半场得出的下半场、以及缺少全场或上半场时的下半场，由单测分开覆盖。
