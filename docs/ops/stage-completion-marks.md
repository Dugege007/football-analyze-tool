# 取数阶段完成标记

用户 2026-11-07 定：每日取数各阶段成功后写下带时间的完成标记；巡检与看门狗平时只看标记与心跳是否新鲜，不要每次从头重扫库；另留很低频抽查（每天中午前历史补数守卫那一次）用来抓住漏打标。

## 标记写在哪里

统一落点目录（可用环境变量 `STAGE_MARKS_DIR` 覆盖）：

- 目录：`/workspace/odds-data/stage-marks/`
- 最新一份：`latest/<阶段名>.json`（每个阶段只保留一份，后写覆盖前写）
- 追加日志：`log/<阶段名>.jsonl`（每次写入追加一行，便于回溯）

库代码在：`football-analyze-tool/scripts/stage_marks/`。

## 标记长什么样

JSON，核心字段如下：

- `schema`：固定为 `stage_mark/v1`
- `stage`：阶段名，例如 `live.tick`、`hist.scan_gaps`、`cn.push`、`fc3d.run`、`backup.daily`
- `status`：只允许 `ok`、`fail`、`partial`、`skipped`
- `finished_at`：北京时间完成时刻（带 `+08:00` 的 ISO 时间）
- `tz`：固定 `Asia/Shanghai`
- `summary`：摘要数字或短说明（场次数、API 调用数、竞彩日等）
- `source`：写入方脚本路径或短名
- `refs`：可选，指向心跳、pending、报告等原有文件

**硬规则：** 只在该阶段真正成功（或明确失败收尾）之后写入；禁止在阶段开始前就打 `status=ok`。

## 典型阶段

| 阶段名 | 含义 | 谁来写 |
| --- | --- | --- |
| `live.tick` | 实时采集完成一轮 tick | `scripts/live/live_capture.py` |
| `live.open_batch` | 本轮抓到了开盘／11:10 相关目标 | 同上 |
| `live.mid_batch` | 本轮抓到了中盘目标 | 同上 |
| `live.close_batch` | 本轮抓到了临盘目标 | 同上 |
| `live.day_summary` | 某竞彩日 summary 写完 | 同上 |
| `live.prematch_standings` | 本轮赛前积分榜快照写完（没有可写场次也算完成） | `scripts/live/live_capture.py` 调用 `scripts/live/prematch_standings.py` |
| `hist.scan_gaps` | 历史补数扫缺口入队完成 | `odds-data/.../build_fullmatch_queue.py` |
| `hist.pull` | 历史补数拉数一轮结束 | `odds-data/.../run_fullmatch_queue.py` |
| `cn.prepare` | 大陆站打包（pending）完成 | `auto-cn-push-after-close/.../run_auto_cn_push.py` |
| `cn.push` | 大陆站推送成功并已 mark-pushed | 同上 |
| `fc3d.run` | 福彩 3D 晚间 `run` 完成 | `fc3d/monitor/fc3d_monitor.py` |
| `backup.daily` | 正式库每日备份完成 | `api/scripts/backup_daily.py` |

既有状态不会推倒：实时心跳仍在 `5dollar/live/logs/heartbeat.json`；大陆站仍有 `markers/pending_*.json` 与 `pushed_*.json`；福彩 3D 仍有 `last_run.json`；备份仍有 `last_backup.json`；历史队列仍有 `queue/state.json`。统一标记是在这些之上补一层给巡检读的完成态。

## 巡检怎么读

不要做近 7 日全库缺口重扫。直接跑：

```bash
cd /workspace/football-analyze-tool
PYTHONPATH=scripts python3 -m stage_marks.check --profile live
PYTHONPATH=scripts python3 -m stage_marks.check --profile all --json
```

画像说明：

- `live`：看心跳是否新鲜，以及 `live.tick` 是否新鲜（批次标记为参考）
- `hist`：看 `hist.scan_gaps`（以及 `hist.pull`）
- `cn`：看 `cn.prepare` / `cn.push`
- `daily`：看福彩 3D 与每日备份
- `all`：全部列出

退出码 `0` 表示所需项新鲜且非失败；`1` 表示有过期、失败或硬性缺失。

过渡期：若统一标记尚未写出，检查入口会只读回退到 `last_run.json`、`last_backup.json`、历史 `state.json` 等遗留文件，不会因此去扫库。

## 中午抽查怎么用

每天中午前的历史补数守卫仍负责低频抽查：先扫缺口，有漏才补，只剩无法填的缺口则直接结束。扫完后应留下 `hist.scan_gaps` 标记；若有拉数，再留下 `hist.pull`。平时 15 分钟唤醒与每小时守护检查只读标记与心跳，不再把近 7 日 gap-scan 加回例行提示词。

若抽查发现「库里有漏但标记显示成功」，以库为准修数据，并检查对应主路径是否漏写标记。
