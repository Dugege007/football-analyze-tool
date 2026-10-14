/**
 * Team display names for the 主队 and 客队 columns, following the naming standard 分析师 set on 2026-10-14.
 *
 * Sources: odds-data/research/dc_train/team_aliases_proposed.csv, with
 * odds-data/backfill/dc-aliases-review-2026-10-08/corrections.csv winning on conflicts (the approved set in REVIEW.md section 7),
 * together with the teams and team_aliases tables (rules in docs/schema/data-conventions-v1.md section 2).
 * Aliases that are valid only in some leagues come from LEAGUE_SCOPED below.
 * Copied by hand into this file on 2026-10-14. When the backend API returns standard names, delete this table and read the API field instead.
 * Teams in the KuPS group and teams with no record show the source name unchanged;
 * they are listed in web/docs/team-names-pending.csv.
 */
const TEAM_DISPLAY: Record<string, string> = {
  "AC奥卢": "奥卢",
  "FC安养": "安养FC",
  "IFK哥德堡": "哥德堡",
  "伊蒂哈德": "吉达联合",
  "全北汽车": "全北现代",
  "华沙比斯": "VPS瓦萨",
  "卡马尔": "卡尔马",
  "大田韩亚市民": "大田市民",
  "柏雷索尔": "柏太阳神",
  "浦项制铁": "浦项铁人",
  "科里迪巴": "科里蒂巴",
  "米亚尔比 AIF": "米亚尔比",
  "英特杜古": "图尔库国际",
  "迪加佛斯": "代格福什",
  "金泉尚武FC": "金泉尚武",
  "韦斯特罗斯": "瓦斯特拉斯",
  "首尔": "FC首尔",
}

/** Other recorded names of a team, keyed by its display name, for the hover tooltip. */
const TEAM_OTHER_NAMES: Record<string, string[]> = {
  "AC 米兰队": ["AC米兰"],
  "FC首尔": ["首尔"],
  "KFUM": ["奥斯陆KFUM"],
  "PEC兹沃勒": ["兹沃勒"],
  "RKC华域克": ["瓦尔韦克"],
  "TPS图尔库": ["特普寺"],
  "VPS瓦萨": ["华沙比斯"],
  "乌特勒支": ["乌德勒支"],
  "云达不莱梅": ["云达不来梅"],
  "代格福什": ["迪加佛斯"],
  "伊普斯威奇": ["伊普斯维奇"],
  "全北现代": ["全北汽车"],
  "冈山绿雉": ["冈山雉鸡"],
  "利兹": ["利兹联"],
  "利雅得希拉尔": ["利雅得新月"],
  "南安普顿": ["南安普敦"],
  "卡尔马": ["卡马尔"],
  "古比斯": ["PAUN古比斯"],
  "吉达联合": ["伊蒂哈德"],
  "哈姆斯塔德": ["哈尔姆斯塔德", "哈尔姆斯达斯"],
  "哥德堡": ["IFK哥德堡"],
  "图尔库国际": ["英特杜古"],
  "埃尔维斯": ["FC爱尔维斯"],
  "塞尔塔": ["维戈塞尔塔"],
  "塞纳乔其": ["SJK塞伊奈约基", "塞伊奈约基"],
  "墨尔本胜仗": ["墨尔本胜利"],
  "大田市民": ["大田韩亚市民"],
  "天狼星": ["斯瑞斯"],
  "奥卢": ["AC奥卢"],
  "奥尔格里特": ["奧基迪"],
  "安养FC": ["FC安养"],
  "富川FC": ["富川FC 1995"],
  "巴黎圣日耳曼": ["巴黎圣日尔曼"],
  "帕特博恩": ["帕德博恩"],
  "拉科鲁尼亚体育": ["拉科鲁尼亚"],
  "曼城": ["曼彻斯特城"],
  "曼联": ["曼彻斯特联"],
  "朴茨茅斯": ["朴次茅斯"],
  "柏太阳神": ["柏雷索尔"],
  "柏林联": ["柏林联合"],
  "格尼斯坦": ["IF 吉尼斯坦"],
  "江原FC": ["江原 FC"],
  "沙尔克": ["沙尔克04"],
  "济州SK FC": ["济州联"],
  "浦项铁人": ["浦项制铁"],
  "瓦斯特拉斯": ["韦斯特罗斯"],
  "磐城FC": ["Iwaki SC"],
  "科里蒂巴": ["科里迪巴"],
  "米亚尔比": ["米亚尔比 AIF"],
  "索尔纳": ["AIK俱乐部"],
  "纽卡斯尔": ["纽卡斯尔联"],
  "纽卡斯尔喷射机": ["纽卡斯尔喷气机"],
  "维京FK": ["维京"],
  "蔚山HD": ["蔚山现代"],
  "西汉姆": ["西汉姆联"],
  "赫尔": ["赫尔城"],
  "金堡尔": ["坎布尔"],
  "金泉尚武": ["金泉尚武FC"],
  "阿尔华卡": ["阿尔维卡"],
  "雅罗": ["查路"],
  "霍芬海姆TSG": ["霍芬海姆"],
}

/**
 * League-scoped team aliases, copied row for row from
 * odds-data/backfill/dc-aliases-review-2026-10-08/league_scoped_aliases.csv.
 * Copied row for row on 2026-10-14; delete it when the backend API returns standard names.
 * Each alias maps to its team only when the match's standard league name (after league mapping)
 * is one of the listed leagues; elsewhere the source name is shown. Add new rows here when that file changes.
 */
const LEAGUE_SCOPED: { alias: string; team: string; leagues: string[] }[] = [
  { alias: "阿赫利", team: "吉达国民", leagues: ["沙特联", "亚冠", "亚精英赛"] },
]

/** Returns the scoped team for an alias in a league, or null when no scoped row applies. */
function scopedTeam(name: string, leagueDisplay: string | null | undefined): string | null {
  if (!leagueDisplay) return null
  const hit = LEAGUE_SCOPED.find((r) => r.alias === name && r.leagues.includes(leagueDisplay))
  return hit ? hit.team : null
}

/** Returns the display name for a team, preferring the backend canonical name when one exists. */
export function teamCommon(name: string | null | undefined, canonical?: string | null, leagueDisplay?: string | null): string | null {
  const base = (canonical || name)?.trim()
  if (!base) return null
  return scopedTeam(base, leagueDisplay) ?? TEAM_DISPLAY[base] ?? base
}

/** Returns the hover text with the source name and the other recorded names, or undefined when there are none. */
export function teamAliasTip(name: string | null | undefined, canonical?: string | null, leagueDisplay?: string | null): string | undefined {
  const shown = teamCommon(name, canonical, leagueDisplay)
  if (!shown) return undefined
  const others = new Set<string>(TEAM_OTHER_NAMES[shown] ?? [])
  if (name) others.add(name)
  if (canonical) others.add(canonical)
  others.delete(shown)
  return others.size ? `其他名称：${[...others].join('、')}` : undefined
}
