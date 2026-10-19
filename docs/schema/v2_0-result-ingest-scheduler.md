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

机上例行拉起请使用仓库内看门狗 `ops/ensure_result_ingest.sh`。定时任务写法见该脚本头部注释。本变更合并进主分支之后，再把机上例行任务从仓库外的 `football-analyze-tool-ops/result-ingest-watchdog/ensure_result_ingest.sh` 改到这一条路径；仓库外那份只保留作过渡。

密钥与数据库路径不要写入本仓库。看门狗读取环境变量 `RESULT_INGEST_ENV` 指向的文件；若该变量未设置，则读取本机过渡文件 `/workspace/football-analyze-tool-ops/result-ingest-watchdog/.env.result_ingest`。键名示例（只有空占位，没有真实密钥）见 `ops/.env.result_ingest.example`。脚本会取消环境变量 `LIVE_CAPTURE_CREATE_MATCHES`，也不打开双写。

输出目录里的 `daemon.lock` 未被占用时，看门狗只拉起一份常驻进程，安静成功时退出码为 0。该锁仍被占用，且心跳文件 `heartbeat.json` 的修改时间距今超过 600 秒，或心跳文件不存在时，向标准输出打印 `HEARTBEAT_STALE` 并退出 2；此时不结束已有进程，也不再拉起第二份。需要启动但缺少 `FIVEDOLLAR_FOOTBALL_API_KEY` 时，打印 `START_FAILED_NO_KEY` 并退出 3。拉起前会检查 `/workspace/odds-data/.venv-dc`（可用 `RESULT_INGEST_VENV` 覆盖）能否 `import numpy, scipy`；缺包时只按 `api/requirements.txt` 安装这两项（不装 fastapi 整栈）。建虚拟环境失败打印 `START_FAILED_VENV`，装包或复检失败打印 `START_FAILED_DEPS`，均退出 4。默认输出目录是 `/workspace/odds-data/backfill/result-ingest-2026-10-14`，默认正式库是 `/workspace/match-analysis-api/data/app.db`。合并标志 `FORMAL_MERGED.flag` 与这份正式库放在同一目录；文件不存在时，看门狗会创建空文件。

手工启动（与看门狗拉起的命令相同）仍然是：

```
nohup setsid /workspace/odds-data/.venv-dc/bin/python scripts/live/result_ingest.py daemon \
  --out-dir /workspace/odds-data/backfill/result-ingest-2026-10-14 > …/nohup.out 2>&1 &
```

- 单实例锁是输出目录里的 `daemon.lock`（文件锁 `flock`），第二份启动会直接退出。
- 输出目录里有 `daemon.log`（每轮日志）、`heartbeat.json`、`state.json`（每场上次拉取时刻）、`call_log.tsv`（每次接口请求和剩余额度）、`csl_raw/`、`sporttery_raw/`，以及每 30 分钟重写一次的 `daily_summary.md`。
- `backfill` 子命令只用本地已刷新的缓存补录，不受 6 小时截止限制，用于一次性补缺口。

## 竞彩网缓存每日刷新（2026-10-14 分析师决定；2026-10-17 起大陆本机取数改为 YPC 优先）

本机出口请求竞彩网会被拦。因此每天北京时间 11:30 之前，先在大陆本机 YPC（即 YuePC）上请求一次，再把结果拷回本机导入。YPC 不在线，或者在 YPC 上取不到文件时，才改用 MSI。只有 YPC 与 MSI 都失败时，才把这一次刷新记为失败。

1. 先在 YPC 上用 Windows PowerShell 5.1 执行下面的命令。YPC 上的 `pwsh` 只是 WindowsApps 里的占位程序，不能运行这个脚本。脚本位于仓库 `scripts/live/msi/refresh_sporttery.ps1`，实测放在 `C:\Users\Administrator\fat-sporttery\`。它请求最近 4 天到今天的赛果，并把所有分页合成一个文件 `latest_bundle.json`。

   `powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Administrator\fat-sporttery\refresh_sporttery.ps1`

2. 把 YPC 上的 `C:\Users\Administrator\fat-sporttery\latest_bundle.json` 拷到本机 `/workspace/uploads/sporttery_bundle/latest_bundle.json`。`D:\Projects\fat-sporttery\` 不是取数主路径，该盘写入曾经被拒绝。只有文件已经写成功之后，才可以再复制一份到这个目录，这一份只是可选副本。

3. 如果第 1 步或第 2 步失败，再在 MSI 上用 `pwsh` 执行下面的命令，并把 MSI 上的 `C:\Users\win10\fat-sporttery\latest_bundle.json` 拷到本机的同一路径。MSI 才使用 `pwsh` 和 `C:\Users\win10\fat-sporttery\`。

   `pwsh -NoProfile -ExecutionPolicy Bypass -File C:\Users\win10\fat-sporttery\refresh_sporttery.ps1`

4. 本机执行：`python scripts/live/result_ingest.py sporttery_import --bundle /workspace/uploads/sporttery_bundle/latest_bundle.json`。如果 YPC 与 MSI 都不在线，或者两台电脑都没有拿到文件，也要执行一次导入命令，并且改成 `--note "原因"`、不带 `--bundle`。这样每日汇总里会记一次失败。

导入时把分页写进 `/workspace/odds-data/research/jingcai-competition-whitelist/cache/sporttery/msi_<开始>_<结束>_p<页>.json`。文件名前缀仍然使用 `msi_`，以便兼容已经落盘的历史缓存。是否把这个前缀改名，可以另议，本次不改。导入还会在输出目录的 `sporttery_refresh.jsonl` 追加一条记录。`daily_summary.md` 的「竞彩网缓存刷新」列按这些记录写成功或失败。文件生成超过 3 小时、大陆本机请求失败，或者分页内容不对，都记为失败。这里的大陆本机请求失败，是指已经先试过 YPC、失败后再试过 MSI，并且两台都失败。

## 只有一个来源的场次（竞彩网或 5DF，2026-10-14 分析师规则）

`result_ingest_log.source` 为 `5df_csl` 或 `sporttery` 的场次用 Football-Data.co.uk 公开 CSV 再核对一次（常驻程序每 12 小时最多下载一次 CSV，
每 30 分钟核对最近 7 天；手动执行 `result_ingest.py crosscheck [--fd-refresh] [--dry-run]`）：

- 对上且比分一致：`source` 改为 `5df_csl+football_data_co_uk`，`fd_score` 写入。
- 对上但比分不一致：`result_ingest_flags.flag = 'fd_score_mismatch'`，赛果保留，等人工抽查。
- 对不上：`result_ingest_flags.flag = 'single_source'`（单一来源）。以后核对上了会把这个标记的 `resolved_at` 填上。

中文队名到 Football-Data 英文名的对应来自定稿表 teams.csv 的英文别名、库里 team_aliases 的英文别名，以及输出目录的 `fd_team_map.csv`（手工补充）。

## 数据表接口里的退款、推迟、取消、腰斩

`GET /table/matches` 读取 `result_ingest_flags` 里未解决的 refund、postponed、cancelled、abandoned 标记（表不存在时，例如正式库，就不输出）：

- 嵌套格式：`result.status`，取值 `finished`（有完场比分）、`refund`、`postponed`、`cancelled`、`abandoned`；特殊场次的比分各列为 null。
  同时 `result_hidden_reason` 和 `hidden_reason` 等于同一个值，`settlement` 为 null。
- 扁平格式：列名 `result_status`。
- 没有赛果也没有标记时，`result` 仍为 null（扁平格式 `result_status` 为 null），`result_hidden_reason` 照旧。

`daily_summary.md` 末尾的「待抽查场次」表逐场列出未解决的 single_source 和 fd_score_mismatch（标记、编号、竞彩日、对阵、比分、来源），供分析师每日抽查。
`sporttery_import --dry-run` 只检查文件，不写缓存，也不记刷新记录。
