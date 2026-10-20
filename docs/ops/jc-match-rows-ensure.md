# 竞彩场次行：写入正式库与看门狗

竞彩日 `matches` 行只由 `api/scripts/build_jc_match_rows.py` 的 `apply` / `daemon` / `ensure-daemon` 写入。

`LIVE_CAPTURE_CREATE_MATCHES`（以及 live_capture 的 `--create-missing-matches`）只给实时采集补 stub 场次，**不是**这条路径。保持默认 `0`。缺竞彩场次行时跑本脚本的 ensure，不要打开那个开关。

## FORMAL_MERGED

研究副本并入正式库之后，本脚本允许写 `APP_DB_PATH` 指向的正式 `app.db`。下列任一成立即为已合并：

1. 环境变量 `FORMAL_MERGED=1`
2. **权威位置**：`Path(APP_DB_PATH).resolve().parent / FORMAL_MERGED.flag`  
   与正式库文件同目录。`APP_DB_PATH` 指向现网库时以这一处为准。  
   机上实际位置示例：`/workspace/match-analysis-api/data/FORMAL_MERGED.flag`（旁边是 `app.db`）。
3. 兼容位置：`$MA_API_ROOT/data/FORMAL_MERGED.flag`  
   `api/data` 软链到正式数据目录时，与第 2 处是同一文件。

两处 flag 都不存在且未设环境变量时，仍拒绝写正式库（只允许 `v2d3/app.db`）。日志 `api/logs/jc_match_rows.jsonl` 的 `run` / `run_error` 带 `db` 与 `formal_merged`。

## fat-run-main

在 fat-run-main 里跑本脚本时，默认的 `MA_API_ROOT/data` 往往不是正式数据目录，flag 会漏检，`_FORMAL_MERGED` 为假，于是拒绝写正式库；写入目标若仍落在已 `chmod 444` 的 v2d3 上，会报 `attempt to write a readonly database`。

任选其一，让路径指到正式库：

- 把 `football-analyze-tool/api/data` 软链到正式数据目录（例如 `/workspace/match-analysis-api/data`）
- 或把 `MA_API_ROOT` 指到 match-analysis-api（其 `data/FORMAL_MERGED.flag` 与 `data/app.db` 相邻）

并让 `APP_DB_PATH` 指向那份正式 `app.db`。然后在这份已链好数据的 football-analyze-tool 里执行：

```bash
python3 api/scripts/build_jc_match_rows.py ensure-daemon
```

## 看门狗

`ops/ensure_jc_match_rows_watch.sh` 调用 `ensure-watch`：

- 没在跑（锁已放开，含进程已退出）：立刻 `ensure-daemon`
- 仍占锁但 `api/logs/jc_match_rows/heartbeat.json` 超过 `JC_MATCH_ROWS_HEARTBEAT_STALE_SEC`（默认 1200 秒）：先 SIGTERM，必要时 SIGKILL，再拉起

daemon 空闲时约每 20 秒刷新心跳；`apply` 期间不刷新，所以阈值要长于一轮写入。cron 示例见脚本头部注释，每 5 分钟一次即可。

## 未匹配名称

`apply` 在场次行写入之后，把仍没有身份编号的队名和赛事名送进名称入库闸（`api/app/name_resolve_gate.py`）。干跑只给出预览，不写待定池。写入时优先进入待定池，不把五美元原文升成常用名，也不回写 `matches` 上的队名或赛事文本。若目标是正式库，待定池写入仍走备份闸门；备份环境变量没有设置时，场次行保持已写入，报告里的 `name_gate` 记下错误。
