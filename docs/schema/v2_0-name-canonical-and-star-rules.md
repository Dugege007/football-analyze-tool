# 常用名与待确认星号（v2.0）

> 用户 2026-10-18 拍板。下面六条是名称和星号的产品规则，球队与赛事同一套。
> 2026-10-19 补充：星号表示尚未落到用户熟悉的常用名。没有来源名、也没有规范名时标星。
> 2026-10-19 再补：一组等于同一支球队或同一项赛事；新名称如何并入已有组或新建组，见同目录 `v2_0-name-resolve-group-rules.md`。无常用名时须在组内选定默认显示并继续标星。
> 2026-10-20：总方案、同步含义、升格优先级、自动化边界与禁止清单以同目录 `name-system-design-v1.md` 为准。本文只保留字段路径和星号读法。与总方案冲突时改本文，不另起第三套规则。
> 实现见 `api/app/team_pending.py`、`api/app/league_pending.py`，赛程字段见 `api/app/main.py` 的 `build_match_obj`，数据表见 `api/app/table_matches.py`，以及前端 `web/src/names/teamName.ts`、`web/src/names/leaguePending.ts`。前端只跟随接口给出的显示名和待确认布尔值。待定池核心见 `api/app/alias_pool.py`。新字符串入库闸见 `api/app/name_resolve_gate.py`。

## 规则

1. 常用名的首选来源是雷速体育对赛事和球队的写法。
2. 其次采用旧 Football_Analyze 仓库数据中高置信度的名称。
3. 已经确定常用名的，前端不得显示星号 `*`。
4. 尚未确定的名称先搜索再匹配。能并入则只加别名；对不上则新建组，默认显示并继续标星。外部取证可以起草，没有出处时停在待定池；生产进程不抓取网页。升格优先级是雷速体育，其次是旧仓高置信（仅当该实体还没有雷速常用名），人工定稿和用户改正可以随时写入。没有雷速、也没有旧仓高置信时不升格。同步只改映射表和名称包，不把全文替换比赛文本列当作同步。定义见总方案第 3.10 节。
5. 不得把长相相近、但可能是不同实体的名称自动合并。男子与女子、一线队与青年队都属于这种情况。
6. 球队和赛事共用同一套机制。

## 名称组

一个名称组收齐同一支球队、或同一项赛事的全部名称。新名称如何并入已有组或新建一组，只以 `v2_0-name-resolve-group-rules.md` 为准，本文不另写一套步骤。别名和常用名都可以以后再补。

名称组里已经有升格后的常用中文名时，页面显示这个常用名，待确认布尔值为 false，不标星。库里的常用中文名写在 `teams.name_zh_canonical` 或 `leagues.name_zh_canonical`。这一列若仍标着待确认，或升格标记仍是临时名，页面照样显示这一列里的文字，但继续标星。来源文本本身已经是升格后的常用名时，即使这场没有填写编号，也不标星。

名称组还没有常用中文名时，从该组内部挑一条默认显示名。挑选顺序是：先用来源文本里的非空名称，再用实体上另外保存的非空名称，再用别名。别名有多条时，优先选用含有汉字的一条；仍有多条时，按写入顺序取第一条。只要组里还有任意一条非空名称，显示就不得留空。这时待确认布尔值为 true，页面标星。若挑中的来源文本本身已是升格后的常用名，则不标星。来源文本、常用名、实体名和别名全部为空时，显示才可以为空，此时仍然标星，原因码是 `name_absent`。别名即使和某条已升格常用名的文字相同，也不能单独把星号消掉。

外部来源只把写法挂成别名，不能因此升成常用名。统计源 BSD（bzzoiro）属于这类外部来源。常用名的升格仍只走雷速体育、人工明确定稿，或旧分析仓库的高置信名称。别名可以拿来显示，但不得拿来把星号消掉。

身份编号只用来找到名称组。编号本身不能加上星号，也不能去掉星号。已经升格的常用名，即使这场比赛还没填编号，也不标星。

## 星号怎么判

接口布尔值是页面的直接依据。`teams_pending_confirm.home` / `.away`、`home_team_pending_confirm`、`away_team_pending_confirm`、`league_pending_confirm` 为 true 时标星，为 false 时不标星。

名称状态只有两类：

| 状态 | 含义 | 星号 |
|---|---|---|
| 已升格 | 雷速体育写法、人工明确定稿，或旧分析仓库的高置信名称 | 不显示 |
| 临时名、尚未升格，或两边都空 | 已归并到临时名、标了待确认、待定池仍开放、全量名册里没有，或来源名与规范名都空 | 必须显示 |

身份编号不是升格。`team_id`、`league_id` 为空或已填，都不能单独把星号去掉，也不能单独把星号加上。没有来源名、也没有规范名时标星。已经升格的常用名不标星。

历史场次上仍为空的 `home_team_id`、`away_team_id`、`league_id` 用 `api/scripts/backfill_match_entity_ids.py` 回填（默认只读预览，只查不建，只填空槽）。

相近名称各自判断。`切尔西` 已经升格时不标星；`切尔西女`、`曼联青年` 不能因此消掉星号。性别、年龄段、二队和已经点名的同名实体，与待定池禁止自动映射的范围相同。

## 升格标记

名称表用 `promotion` 写出升格来源。`pending_confirm=true` 时一律还是临时名。

| `promotion` | 状态 |
|---|---|
| `leisutiyu`、`雷速体育` | 已升格，首选来源 |
| `human_finalize` | 已升格，人工明确定稿 |
| `legacy_high_confidence` | 已升格，旧分析仓库高置信名称 |
| `user_correct` | 已升格，用户改正后的常用名 |
| `temporary` | 临时名，继续标星 |
| 空白，且 `pending_confirm` 不是真 | 只兼容名称表 CSV，视为已升格 |

上表的空白兼容只用于 `api/resources/name-standard/` 里的名称表，以及同样格式的外部定稿、种子表。正式库 `teams` / `leagues` 的 `name_zh_canonical` 不是升格证明。历史行常把竞彩或训练源原文写进这一列，又没有升格来源。`pending_confirm` 不为真，甚至没有这一列，都不能把该规范名算成常用名。

正式库升格来源读 `promotion_source`，没有该列时读 `promotion`。只认 `leisutiyu`、`雷速体育`、`human_finalize`、`legacy_high_confidence`、`user_correct`（以及与之同义的 `leisu`、`雷速`、`established`）。命中时不标星，并可以从待确认里拿掉。没有来源、或来源不在这组里时：这一字符串若已在名称表的已升格常用名里，保持不标星；否则赛程和数据表的 `*_pending_confirm` 为真。同文字的别名，即使来源不以 `_pending_confirm` 结尾，也不能把这种未升格规范名消星。本次不改写场次原文，也不批量改规范名。

文字已经和常用名相同（`already_same`），或正式库 `name_zh_canonical` 有字但 `promotion_source` 与 `promotion` 都空，都不是升格证明。必须把允许的升格来源写进名称包的 `promotion` 列，并写进正式库的 `promotion_source`（没有该列时写 `promotion`）之后，读路径才把这一行当作已升格。只改比赛主客或赛事文本列不能代替这次写入。旧仓高置信由 `api/scripts/apply_team_direction.py` 在方向已经对齐时补上 `legacy_high_confidence`。雷速升格由 `api/scripts/promote_leisuti_names.py` 写入 `leisutiyu`，并同步名称包。

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

来源是 `leagues.name_zh_canonical`、`teams.name_zh_canonical`，按 `league_id`、`home_team_id`、`away_team_id` 读取。编号为空、对不上标准名行，或规范名为空白时，赛程接口先回退到对应文本列；文本列也是空白、但该名称组里还有别名时，再改用组内默认显示。接口不因此返回 500。`competition.name`、`teams.home`、`teams.away` 仍是场次上的原文，不改写成这份组内默认显示。

数据表在常用名为空、来源文本还有字时，仍把 `league_canonical`、`home_team_canonical`、`away_team_canonical` 留空，页面显示来源文本。来源文本也空、组内还有别名或实体名时，数据表把组内默认显示写进上述字段，页面才不会空白。组内什么名称都没有时，这些字段保持为空。

星号仍以布尔值为准，编号不单独加星，也不单独消星。`teams_pending_confirm.home` / `.away` 与 `home_team_pending_confirm` / `away_team_pending_confirm` 相同。`league_pending_confirm` 与 `competition.league_pending_confirm` 相同。判断把库内常用名（没有则为空，不把回退后的显示名再传一遍）和来源文本一起交给名册，与数据表相同。组内默认显示用到的别名不进入这次判断，因此不能把 `name_absent` 消掉。

## 名册缺失

外部研究目录或 `APP_TEAM_*_FILE` 指向的文件不存在时，记警告，禁止把全部球队或全部赛事标成待确认。已经升格的名称保持不标星，临时名保持标星。

球队包内表只是已升格子集。外部定稿真正读入之后，它才是球队全量名册，名册里没有的队名标为尚未升格。赛事包内表是当前已升格的赛事名册，不在其中的赛事名标为尚未升格。正式库里的 `teams` / `team_aliases`、`leagues` / `league_aliases` 只读并入；表不存在时跳过，不改成全员待确认。并入时，没有升格来源的规范名不写入已升格集合；还不在名称表常用名里的，标为待确认。


## 名称组模型（2026-10-19）

一个常用名对应多个别名。别名来自各平台或应用程序接口的原文写法，用来对场与归组，不直接升格为页面常用名。某平台出现新写法时：先按 `v2_0-name-resolve-group-rules.md` 的七步判断并入已有组还是新建组；能并入则只加别名；不能并入则新建组，默认显示为该新名并标星。

尚未设置常用名的组：必须仍有可显示的默认名（不得留空给前端「无字可画」），并且 `*_pending_confirm` 为真。已经升格的常用名不标星。编号不单独加减星。

BSD 等外文或另一套中文写法属于别名层，不得因为对上了场次就把 BSD 写法写成常用名。

## 验收

赛程和数据表的显示验收、以及写反的常用名如何用对照表脚本检查和替换，见 `docs/schema/v2_0-name-display-acceptance.md`。

新名称并组、新建组、歧义闸与无常用名默认显示的对照审计，以 `v2_0-name-resolve-group-rules.md` 为准。

## 实现状态

并组七步里，哪些已经落在代码里、哪些仍是缺口，写在 `v2_0-name-resolve-group-rules.md` 的「实现状态」。这里只记显示和星号。

无常用名时，赛程和数据表已经用组内默认显示，并保持 `*_pending_confirm` 为真。挑选顺序是来源文本、实体上另存的名称、含汉字优先的别名。新建一组时，默认显示是这条新名；名册尚未升格则待确认为真，不得把 `create=True` 当成常用名升格。外部取证仍不自动抓取，见并组文档第 5 步。

正式库规范名没有升格来源时同样标星。例如「艾尔切队」「哥隆尼斯」若只是竞彩原文写进 `name_zh_canonical`，赛程和数据表为待确认；「AC米兰」「利兹联」已在名称表升格，即使正式库没再写来源，也不标星。写了 `leisutiyu`、`legacy_high_confidence`、`human_finalize` 或 `user_correct` 的行不标星。
