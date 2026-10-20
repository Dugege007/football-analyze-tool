# 赛程与数据表名称显示验收

总方案、同步含义与升格优先级见同目录 `name-system-design-v1.md`。同步只更新映射表（`teams` / `leagues` / 别名表、待确认与升格来源）和 `api/resources/name-standard/` 名称包。比赛行上的主队、客队、赛事文本列可以保留原文，但不是显示权威，也不是同步手段。

赛程和数据表必须使用同一套名称规则。接口给出常用名时，页面显示常用名。接口没有给出常用名、但来源文本还有字时，页面显示来源文本。来源文本和常用名都空、名称组里还有别名或实体名时，页面显示接口写入的组内默认显示，并按待确认布尔值标星。别名只在没有常用名、需要补显示时出现在名称格子里；已经有常用名时，别名仍只出现在悬停列表里。星号只根据接口里的待确认布尔值决定，前端不另写一套规则。

常用名和别名的方向以对照表为准。当前需要记住的两对是：常用名是「利雅得新月」，别名是「利雅得希拉尔」；常用名是「坎布尔」，别名是「金堡尔」。此前名册把这两对写反了。以后检查或替换时，运行对照表脚本，不要每次打开仓库逐个文件翻找。

对照表文件是 `api/resources/name-standard/team-direction-corrections.csv`。脚本是 `api/scripts/apply_team_direction.py`。脚本会检查包内球队名册、前端悬停表和回填种子。给出数据库路径时，脚本再检查 `teams.name_zh_canonical` 和 `team_aliases`。脚本不修改 `matches.home_team`、`matches.away_team` 和 `matches.competition_name`。

只检查、不写文件：

```bash
python api/scripts/apply_team_direction.py
```

检查并替换写反的方向：

```bash
python api/scripts/apply_team_direction.py --apply
```

连同数据库一起检查。把路径换成实际的数据库文件：

```bash
python api/scripts/apply_team_direction.py --db data/app.db
```

连同数据库一起替换。比赛文本列仍然保持原样。方向已经对齐、或本次改为对齐时，若库表有 `promotion_source` 列，脚本写入 `legacy_high_confidence`，且不覆盖已经写成雷速、人工定稿或用户改正的来源。这是升格优先级里的第二档，只在该实体还没有雷速常用名时使用。名称包对应行的 `promotion` 列同样写成 `legacy_high_confidence`。规范名有字但升格来源仍空，不能当成已升格。

```bash
python api/scripts/apply_team_direction.py --db data/app.db --apply
```

雷速升格不走本脚本。干跑、写入映射表和名称包、以及推送后的冒烟期待，见 `docs/ops/name-promote-leisuti.md`。

名册里可以这样核对这两对。常用名列应当是「利雅得新月」和「坎布尔」，对应的别名列应当包含「利雅得希拉尔」和「金堡尔」：

```bash
python api/scripts/apply_team_direction.py
```

数据库里可以在脚本之外用下面的查询再看一眼。查询只用于核对，不代替脚本：

```sql
SELECT name_zh_canonical FROM teams WHERE name_zh_canonical IN ('利雅得新月', '利雅得希拉尔', '坎布尔', '金堡尔');
SELECT alias, team_id FROM team_aliases WHERE alias IN ('利雅得新月', '利雅得希拉尔', '坎布尔', '金堡尔');
```

期待的结果是：`teams.name_zh_canonical` 里有「利雅得新月」和「坎布尔」；「利雅得希拉尔」和「金堡尔」出现在 `team_aliases.alias`，并指向对应的常用名。如果两行球队同时存在，脚本会停下来，不会自动合并。

## 验收清单

- [ ] 赛程上每一个看得见的球队名和赛事名，在接口提供规范名时都显示规范名。数据表上的主队、客队和赛事也是这样。
- [ ] 每一个尚未确定的名称都显示星号。每一个已经升格的名称都不显示星号。来源名和常用名都空时也显示星号。名称组里还有别名或实体名时，格子不得空白。星号使用赛程和数据表共用的待确认布尔值，包括主队、客队和赛事。
- [ ] 「利雅得新月」按常用名显示，「利雅得希拉尔」只出现在悬停别名列表里。「坎布尔」按常用名显示，「金堡尔」只出现在悬停别名列表里。悬停列表是一行一个名称，前面没有「别名：」。
- [ ] 上述两个别名已经记在名册里，并指向对应的常用名。验收时运行对照表脚本做检查或替换，不要每次手工翻找仓库。
- [ ] 只接受「team_id 为空的场次数等于零」并不能通过验收。赛程和数据表都要抽查显示出来的名称是不是规范名，以及星号有没有标对。
