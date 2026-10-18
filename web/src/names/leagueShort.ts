import { formatAliasLines } from './aliasTip.js'

/**
 * League display names for the 赛事 column.
 *
 * Every display name is the name the user recorded manually in the legacy match records
 * (odds-data/imports/predictions/CFFXDJ_5_V3_predictions.csv and the leagues table seeded from them).
 * The link from the backend's full name to that manual name comes from
 * odds-data/research/jingcai-competition-whitelist/competition_alias_seed.csv (canonical_status legacy),
 * which matched every legacy name to the 竞彩 and 5DollarFootballAPI names.
 * Where that file conflicts with the user's decision documents, the decision documents win.
 * 雷速常用名后来覆盖了其中一条：巴西甲级联赛显示为巴西甲，巴甲只作别名
 * （docs/schema/v2_0-name-canonical-and-star-rules.md）。
 * Updated on 2026-10-14 from the finalised standard approved by 分析师:
 * odds-data/research/name-standard-2026-10-14/competitions.csv (with README.md); every alias in its aliases column maps to its canonical name.
 * Copied by hand into this file on 2026-10-14. When the backend API returns standard names, delete this table and read the API field instead.
 * The data sheet, the schedule page, and the match detail header all import this function, so a league keeps one short name.
 * A league with no record shows its source name unchanged.
 */
const LEAGUE_DISPLAY: Record<string, string> = {
  "Africa Cup of Nations Qualification": '非预赛',
  "Baltic Cup": '波罗的海杯',
  "Campeon de Campeones": '墨冠杯',
  "Chile Primera Division Play-off": '智利甲附加赛',
  "China Cup": '中国杯',
  "Copa Sudamericana": '南美杯',
  "Elite Club Friendlies": '俱乐部友谊赛',
  "International Cup of Champions": '国际冠军杯',
  "Recopa Sudamericana": '南美优胜者杯',
  "South Korea K-League 1 Play-Offs": '韩K联附加赛',
  "USA US Open Cup": '美公开杯',
  "亚足联冠军精英联赛": '亚精英赛',
  "亚足联冠军精英联赛二": '亚冠二级',
  "优胜者杯": '南美优胜者杯',
  "俱乐部杯": '南美杯',
  "俱乐部赛": '俱乐部友谊赛',
  "公开赛杯": '美公开杯',
  "南美俱乐部杯": '南美杯',
  "南美解放者杯": '解放者杯',
  "南美超级杯": '南美优胜者杯',
  "国冠杯": '国际冠军杯',
  "墨西哥冠军杯": '墨冠杯',
  "巴甲": '巴西甲',
  "巴西甲级联赛": '巴西甲',
  "德国乙级联赛": '德乙',
  "德国甲级联赛": '德甲',
  "意大利甲级联赛": '意甲',
  "挪威超级联赛": '挪超',
  "日本J2联赛": '日职乙',
  "日本J联赛": '日职联',
  "日本J联赛杯": '日联杯',
  "智利甲级联赛附加赛": '智利甲附加赛',
  "欧洲冠军联赛": '欧冠',
  "欧洲波罗的海杯": '波罗的海杯',
  "欧联杯": '欧联',
  "欧足联协会联赛": '欧协联',
  "沙特阿拉伯职业联赛": '沙特联',
  "法国乙级联赛": '法乙',
  "法国甲级联赛": '法甲',
  "波罗杯": '波罗的海杯',
  "瑞典超级联赛": '瑞典超',
  "精英友谊赛": '俱乐部友谊赛',
  "美国公开赛杯": '美公开杯',
  "美国职业足球大联盟": '美职联',
  "联合足协美国公开杯": '美公开杯',
  "芬兰超级联赛": '芬超',
  "英格兰冠军联赛": '英冠',
  "英格兰超级联赛": '英超',
  "荷兰乙级联赛": '荷乙',
  "荷兰甲级联赛": '荷甲',
  "葡萄牙超级联赛": '葡超',
  "西班牙甲级联赛": '西甲',
  "非洲杯预选赛": '非预赛',
  "韩K2联": '韩K联附加赛',
  "韩K2联（韩职）": '韩K联附加赛',
  "韩国K1联赛": '韩K联',
  "韩国K联赛附加赛": '韩K联附加赛',
}

/** 定稿里已经有常用名的赛事：简称本身，以及会映射到简称的来源名。 */
const ESTABLISHED_LEAGUE_NAMES = new Set<string>([
  ...Object.keys(LEAGUE_DISPLAY),
  ...Object.values(LEAGUE_DISPLAY),
])

/** Returns the manual league name, or the source name when there is no manual record. */
export function leagueShort(name: string | null | undefined): string | null {
  if (!name) return null
  return LEAGUE_DISPLAY[name.trim()] ?? name
}

/** 来源名或简称已经在升格后的赛事常用名里。规则见 docs/schema/v2_0-name-canonical-and-star-rules.md。 */
export function leagueNameEstablished(name: string | null | undefined): boolean {
  if (!name) return false
  return ESTABLISHED_LEAGUE_NAMES.has(name.trim())
}

/** 简称对应的其他写法，顺序与 LEAGUE_DISPLAY 一致。常用名逻辑仍只走 leagueShort。 */
const LEAGUE_OTHER_NAMES: Record<string, string[]> = {}
for (const [alias, shortName] of Object.entries(LEAGUE_DISPLAY)) {
  if (alias === shortName) continue
  const list = LEAGUE_OTHER_NAMES[shortName] ?? []
  list.push(alias)
  LEAGUE_OTHER_NAMES[shortName] = list
}

/**
 * 赛事名悬停文案，与队名同一套。
 * 有别名时一行一个，不加前缀。
 * 没有别名时返回 fallback（原来的来源名）；fallback 为空则不显示悬停。
 */
export function leagueAliasTip(
  shown: string | null,
  fallback?: string | null,
  extra?: Array<string | null | undefined>,
): string | undefined {
  if (shown) {
    const others = new Set<string>(LEAGUE_OTHER_NAMES[shown] ?? [])
    for (const item of extra ?? []) {
      const text = item?.trim()
      if (text) others.add(text)
    }
    others.delete(shown)
    const trimmed = shown.trim()
    if (trimmed !== shown) others.delete(trimmed)
    const lines = formatAliasLines(others)
    if (lines) return lines
  }
  const rest = fallback?.trim()
  return rest || undefined
}
