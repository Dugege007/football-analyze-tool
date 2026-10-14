# 赛果定时入库程序说明（v2.0，2026-10-14）

本程序把用户 2026-10-07 拍板的入库节奏（见 `v2_0-result-ingest-schedule.md`）做成常驻任务，脚本是 `scripts/live/result_ingest.py`，单元测试是 `scripts/live/test_result_ingest.py`。

## 节奏

- 每 60 秒检查一次本地研究副本 v2d3 里已经开赛、还没有赛果的场次，这一步不调用任何接口。
- 开赛后满 3 小时做第一次拉取；还没有赛果的，距上次拉取满 30 分钟再补拉一次；开赛后满 6 小时停止拉取，并标记 `no_result_after_cutoff`，挂到日核对队列。
- 每次拉取时，对到点场次所在的每个竞彩日调用两次 5DollarFootballAPI 的 `/chinasportslottery`（与 live_capture 的两个时间窗一致），再尝试直连竞彩网赛果接口一次。本机出口访问竞彩网会被拦截（HTTP 567），被拦时改读本地竞彩赛果缓存。

## 写库规则

- 只写研究副本 `…/v2d3/app.db`，路径是正式库 `data/app.db` 或不以 `v2d3/app.db` 结尾时直接拒绝运行。
- 每次有内容要写之前，先用 sqlite backup 备份到 `/workspace/backups/v2d3-before-result-ingest-<时间戳>/app.db`。程序只清理自己的备份目录，保留最近 24 份，外加每天第一份。
- `results` 只插入缺失的行，已有的行一律不覆盖。
- 竞彩网与 5DF 两个来源都有比分且不一致时，不写入，记 `score_conflict`。只有一个来源有比分时按该来源写入，并在来源表里记明。
- 竞彩网与 5DF 的对齐方式：5DF 按「竞彩日加编号」对齐；竞彩网的 `matchDate` 是开赛的自然日，所以按「竞彩日或次日加编号」对齐，主客两队与库里队名一个相同的字都没有时视为没对上。

## 新增的表（只新增，不改已有表）

`result_ingest_log`：每场由本程序写入的赛果一行。字段是 `match_id`、`source`（取值为 `sporttery`、`5df_csl`、`sporttery+5df_csl`、`football_data_co_uk`）、`sporttery_score`、`fivedf_score`、`fd_score`、`detail_json`、`batch_id`（对应 `import_batches`）、`ingested_at`。

`result_ingest_flags`：特殊场次标记，主键是 `(match_id, flag)`。`flag` 的取值是 `score_conflict`（比分冲突）、`refund`（竞彩退款）、`postponed`（推迟）、`cancelled`（取消）、`abandoned`（腰斩）、`no_result_after_cutoff`（开赛 6 小时后仍无赛果）。其余字段是 `detail_json`、`first_seen_at`、`last_seen_at`、`resolved_at`。之后补到赛果时，`no_result_after_cutoff` 会写上 `resolved_at`。

推迟场次与已有的 `matches.postpone_void_check`、`matches.manual_review` 口径不冲突：本程序不改 `matches` 表，只在标记表里记录，由接口层以后决定是否把这些标记并入 `daily_check`。标记的场次都不进可结算赛果。

## 运行

```
nohup setsid /workspace/odds-data/.venv-dc/bin/python scripts/live/result_ingest.py daemon \
  --out-dir /workspace/odds-data/backfill/result-ingest-2026-10-14 > …/nohup.out 2>&1 &
```

- 单实例锁是输出目录里的 `daemon.lock`（`flock`），第二份启动会直接退出。
- 输出目录里有 `daemon.log`（每轮日志）、`heartbeat.json`、`state.json`（每场上次拉取时刻）、`call_log.tsv`（每次接口请求和剩余额度）、`csl_raw/`、`sporttery_raw/`，以及每 30 分钟重写一次的 `daily_summary.md`。
- `backfill` 子命令只用本地已刷新的缓存补录，不受 6 小时截止限制，用于一次性补缺口。
