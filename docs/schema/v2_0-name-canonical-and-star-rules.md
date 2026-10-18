# 常用名与待确认星号（v2.0）

> 用户 2026-10-18 拍板。下面六条是名称和星号的产品规则，球队与赛事同一套。
> 实现见 `api/app/team_pending.py`、`api/app/league_pending.py`，赛程字段见 `api/app/main.py` 的 `build_match_obj`，以及前端 `web/src/names/teamName.ts`、`web/src/names/leaguePending.ts`。

## 规则

1. 常用名的首选来源是雷速体育对赛事和球队的写法。
2. 其次采用旧 Football_Analyze 仓库数据中高置信度的名称。
3. 已经确定常用名的，前端不得显示星号 `*`。
4. 尚未确定的名称：先用网络检索帮助归并哪些别名是同一支球队或同一项赛事，并先映射到一个临时名；在升格为常用名之前，前端必须继续显示星号。升格只来自雷速体育确认，或人工明确定稿。
5. 不得把长相相近、但可能是不同实体的名称自动合并。男子与女子、一线队与青年队都属于这种情况。
6. 球队和赛事共用同一套机制。

## 星号怎么判

接口布尔值是页面的直接依据。`teams_pending_confirm.home` / `.away`、`home_team_pending_confirm`、`away_team_pending_confirm`、`league_pending_confirm` 为 true 时标星，为 false 时不标星。

名称状态只有两类：

| 状态 | 含义 | 星号 |
|---|---|---|
| 已升格 | 雷速体育写法、人工明确定稿，或旧分析仓库的高置信名称 | 不显示 |
| 临时名或尚未升格 | 已归并到临时名、标了待确认、待定池仍开放、全量名册里没有 | 必须显示 |

身份编号不是升格。`team_id`、`league_id` 为空或已填，都不能单独把星号去掉，也不能单独把星号加上。没有来源名、也没有规范名时不标星。

历史场次上仍为空的 `home_team_id`、`away_team_id`、`league_id` 用 `api/scripts/backfill_match_entity_ids.py` 回填（默认只读预览，只查不建，只填空槽）。

相近名称各自判断。`切尔西` 已经升格时不标星；`切尔西女`、`曼联青年` 不能因此消掉星号。性别、年龄段、二队和已经点名的同名实体，与待定池禁止自动映射的范围相同。

## 升格标记

名称表用 `promotion` 写出升格来源。`pending_confirm=true` 时一律还是临时名。

| `promotion` | 状态 |
|---|---|
| `leisutiyu`、`雷速体育` | 已升格，首选来源 |
| `human_finalize` | 已升格，人工明确定稿 |
| `legacy_high_confidence` | 已升格，旧分析仓库高置信名称 |
| `temporary` | 临时名，继续标星 |
| 空白，且 `pending_confirm` 不是真 | 兼容旧表，视为已升格 |

提议别名表里没有升格标记的行，只是归并建议：只把尚不在常用名里的名称标星，不得把包内或正式库已经升格的常用名重新打成待确认。

当前包内 `api/resources/name-standard/teams.csv` 与 `competitions.csv` 里，除明确标成临时名的行，以及已经按雷速常用名改写的行以外，记为 `legacy_high_confidence`。这批名称来自 2026-10-14 已采纳的高置信写法，不是雷速体育的新抓取。以后用雷速体育确认过的写法覆盖时，把对应行改成 `leisutiyu`。赛事显示以雷速常用名为准。巴西甲级联赛的页面显示名是「巴西甲」，「巴甲」仅作别名。

## 赛程列表上的规范名

`GET /matches?date=YYYY-MM-DD` 和 `GET /matches/{match_id}` 共用 `build_match_obj`。`/dispatch/pending` 也走这个构造，字段相同。`competition.name`、`teams.home`、`teams.away` 仍是 `matches` 上的文本列，不改写。

赛程页显示规范名时读下面任一路径。同一响应里，嵌套字段和扁平字段是同一个字符串。

| 显示 | 嵌套路径 | 扁平路径（与 `GET /table/matches` 同名） |
|---|---|---|
| 赛事 | `match.competition.canonical` | `match.league_canonical` |
| 主队 | `match.teams.home_canonical` | `match.home_team_canonical` |
| 客队 | `match.teams.away_canonical` | `match.away_team_canonical` |

来源是 `leagues.name_zh_canonical`、`teams.name_zh_canonical`，按 `league_id`、`home_team_id`、`away_team_id` 读取。编号为空、对不上标准名行，或规范名为空白时，上述字段回退到对应文本列，接口不因此返回 500。数据表在同样情况下把 `league_canonical` 等留空；赛程接口回退，是为了页面可以直接显示规范名字段。

星号仍以布尔值为准，编号不单独加星，也不单独消星。`teams_pending_confirm.home` / `.away` 与 `home_team_pending_confirm` / `away_team_pending_confirm` 相同。`league_pending_confirm` 与 `competition.league_pending_confirm` 相同。判断把库内规范名（没有则为空，不把回退后的文本再传一遍）和来源文本一起交给名册，与数据表相同。

## 名册缺失

外部研究目录或 `APP_TEAM_*_FILE` 指向的文件不存在时，记警告，禁止把全部球队或全部赛事标成待确认。已经升格的名称保持不标星，临时名保持标星。

球队包内表只是已升格子集。外部定稿真正读入之后，它才是球队全量名册，名册里没有的队名标为尚未升格。赛事包内表是当前已升格的赛事名册，不在其中的赛事名标为尚未升格。正式库里的 `teams` / `team_aliases`、`leagues` / `league_aliases` 只读并入；表不存在时跳过，不改成全员待确认。

## 验收

赛程和数据表的显示验收、以及写反的常用名如何用对照表脚本检查和替换，见 `docs/schema/v2_0-name-display-acceptance.md`。
