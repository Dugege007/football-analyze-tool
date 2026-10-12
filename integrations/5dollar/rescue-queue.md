# rescue_queue（实时漏点补救队列）

口径权威卡：`docs/schema/v2_0-live-miss-rescue-rules.md`（§7 多日缺口）。

## 目录

| 路径 | 用途 |
|---|---|
| `pending/` | 待消费（`mark_missed` / `gap_scan` 写入；**热路径不调 API**） |
| `done/` | 已结案（asof 成功或 no_odds） |
| `failed/` | 超重试 / 永久失败 |
| `shadow/` | R-D 晚补索引报告（主值已写入规则格；关即时推荐） |
| `logs/` | 入队 / 消费 jsonl |
| `reports/` | `gap_scan_*.json`、`rescue_worker_*.json` |
| `schema_example.json` | 单条字段样例 |

## 接线

| 步骤 | 命令 |
|---|---|
| 单场漏点入队 | `live_capture` tick → `mark_missed` → pending |
| 多日缺口扫描 | `scripts/rescue_gap_scan.py --days 7` 或 `live_capture.py gap-scan` |
| 空闲窗消费 | `scripts/rescue_worker.py` 或 `live_capture.py rescue-consume` |
| 手动 asof | `live_capture.py asof-backfill --date D --target-key …` |

**ensure-daemon 恢复后**：先 `gap-scan`，再在非让路窗 / 非 due 邻近时 `rescue-consume`。勿塞进 tick。

## 默认参数

| 项 | 值 |
|---|---|
| 回溯天数 | **7** |
| 单轮消费 | **8** |
| 日上限（成功/late/no_odds） | **40** |
| 开赛后 >3h | 规则格走 **R-D**；真实格走 **R-F** 仍写 actual。两者 `recommend_live_ok=false`，准确 as-of 仍 `sim_ok/features_ok=true` |
| approx | 中盘 **120**min / 临盘 **60**min；`far_open` 默认可标采纳 |
| 禁消费 | 让路窗 + 11:00–11:20；due 前 15min |

## 消费禁区

- `shared_api_yield` 让路窗：11:05–11:20、14:55–15:15、21:55–22:15
- 额外：11:00–11:20 不消费（避开 11:10 高峰）
- 禁止用 T 后即时盘冒充规则 mid/close 或真实 t8/t1
- 真实通道（R-F）与规则通道同一 Strict as-of：写 `channel=actual`、`point=t8|t1`，`rescue_rule=R-F`，不写规则格。补写行 `asof_backfill=true`、`phase_assign_late=true`（live 不计命中）。特征只收决策时刻截断后仍合法的水位

真实队列条目试跑（不调 API、不写库）：

```
python scripts/live/rescue_worker.py --dry-run --ignore-due-avoid
```

`pending/` 里放一条 `channel=actual`、`point=t1`、`phase_variant=real` 的队列 JSON 时，输出 `action` 应为 `would_asof` 或 `would_asof_late`，`rescue_rule` 为 `R-F`，不再是 `defer_real_R-F`。
