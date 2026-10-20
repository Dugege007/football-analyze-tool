# 雷速升格与旧仓高置信

总方案见 `docs/schema/name-system-design-v1.md` 第 5、7、8、10 章。同步只改映射表和名称包。比赛主队、客队、赛事文本列保持原文，不作为同步手段。

升格优先级是：

1. 雷速体育（`leisutiyu`）。用本节的雷速脚本。
2. 旧仓高置信（`legacy_high_confidence`）。仅当该实体还没有雷速常用名。方向对照脚本在名称已经对齐时把这个来源写入名称包和数据库。文字相同但来源仍空，不算已升格。
3. 两者都没有时不升格，页面继续默认显示并标星。

人工定稿和用户改正可以随时写入。雷速脚本遇到这两种来源且常用名不同时会跳过，避免批次覆盖人工改正。

## 雷速：先干跑

输入是 CSV 或 JSON 数组。可以直接给 `kind` 与 `entity_id`，也可以给开赛时间、主客队名，可选比分，再用 `side` 指定升格哪一侧。开赛窗口是前后十五分钟。对不上或不止一场时跳过。

```bash
python api/scripts/promote_leisuti_names.py \
  --input /path/to/leisuti.csv \
  --db data/app.db \
  --audit /tmp/leisuti-dry-run.json
```

不带 `--apply` 时不写数据库，也不改 `api/resources/name-standard/teams.csv` 与 `competitions.csv`。审计里的 `match_text_rewritten` 应为 false。每条记录有 `action`：`promote`、`already_same` 或 `skip`，以及 `reason`。

CSV 列示例：

```text
kind,entity_id,preferred_name,side,kickoff_at,home,away,home_goals,away_goals,match_id
team,18,利雅得新月,,,,,,,
team,,曼彻斯特城,home,2026-10-20T20:00:00+08:00,曼城,利物浦,,,
```

## 雷速：确认后写入

```bash
python api/scripts/promote_leisuti_names.py \
  --input /path/to/leisuti.csv \
  --db data/app.db \
  --apply \
  --audit /tmp/leisuti-apply.json
```

写入只做这几件事：

- `teams` 或 `leagues` 的 `name_zh_canonical` 改为雷速写法。
- `promotion_source` 与 `promotion` 写成 `leisutiyu`，`pending_confirm` 写成 false。缺列时只补这三列。
- 旧常用名插入对应别名表，`source` 为 `leisutiyu:` 加批次号。
- 名称包同一行改常用名、别名和 `promotion=leisutiyu`。

审计中的 `rollback` 记下改前的常用名和升格来源，供手工回滚。脚本不提供按日扫改比赛文本的入口。

## 旧仓高置信

对照表是 `api/resources/name-standard/team-direction-corrections.csv`。检查：

```bash
python api/scripts/apply_team_direction.py --db data/app.db
```

确认后：

```bash
python api/scripts/apply_team_direction.py --db data/app.db --apply
```

名称已经是常用名、别名也已经指向该队时，脚本仍会补 `legacy_high_confidence`。已有雷速来源时不覆盖。比赛文本列不改。

## 推送后的大陆站冒烟

名称包要和正式库一起推到大陆站。冒烟本身可以留在运维侧，期待与总方案第 10 章、第 11.5 章一致：

- 抽到已升格实体时，页面显示常用名，不标星。
- 抽到未升格、待定或空名时，仍然标星。
- 名册文件缺失时不得把全部名称标成待确认。
- 冒烟失败则这次发布失败，按审计回滚映射表和名称包，而不是去改某一天的比赛文本。
