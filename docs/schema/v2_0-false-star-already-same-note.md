# 假星防复发：already_same 也必须升格

- 日期：2026-10-20
- 背景：用户多次反馈「旧仓已是常用名却仍标星」。

## 规则补丁（与 v2_0-name-canonical-and-star-rules 一致）

1. 旧 Football_Analyze 与正式库 join 后，若某 `team_id` 的 FA 侧多数名 **已经等于** `name_zh_canonical`（already_same），仍须写入允许的 `promotion_source` 并将 `pending_confirm=false`。
2. **禁止**只做 rename draft、跳过 already_same：跳过改名 ≠ 未升格；未写升格来源时，API overlay 会把 bare 规范名标成待确认。
3. 名称包 `teams.csv` / `competitions.csv` 与正式库必须双写；大陆站推送须同批带上名称包 + 升格 SQL/行，并做赛程 pending smoke。
4. 无高置信证据（无 FA/雷速/映射表）不得猜升格；男女队/青年队不自动合并。

## 2026-10-20 执行摘要

- 一次升格 356 队 + 18 赛事入名称包；真待审约 107 队保留。
- 详见 ops：`football-analyze-tool-ops/promote-already-same-20261020/REPORT.md`。
