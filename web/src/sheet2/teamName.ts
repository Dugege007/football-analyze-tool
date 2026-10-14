/**
 * Common Chinese team names for the 主队 and 客队 columns.
 *
 * The backend returns the source name in match.home_team and match.away_team, and a canonical name in
 * match.home_team_canonical and match.away_team_canonical only for some older matches. There is no alias
 * list in the API, so this table maps the source names seen in the data to the common name used by
 * China Sports Lottery (竞彩) and 500.com. Names that are not in the table are shown unchanged.
 */
const TEAM_COMMON: Record<string, string> = {
  'AC 米兰队': 'AC米兰',
  'FC 萨尔斯堡': '萨尔茨堡',
  'RB 莱比锡': '莱比锡',
  勒万特队: '莱万特',
  艾尔切队: '埃尔切',
  阿拉维斯队: '阿拉维斯',
  费耶诺德队: '费耶诺德',
  '米亚尔比 AIF': '米亚尔比',
  霍芬海姆TSG: '霍芬海姆',
  门兴格拉德巴赫: '门兴',
  托特纳姆热刺: '热刺',
  拉科鲁尼亚体育: '拉科鲁尼亚',
  金泉尚武FC: '金泉尚武',
  帕特博恩: '帕德博恩',
  沙尔克: '沙尔克04',
  PSV埃因霍温: '埃因霍温',
  荷华高斯: '前进之鹰',
  RKC华域克: '瓦尔韦克',
  柏雷索尔: '柏太阳神',
  大阪飞脚: '大阪钢巴',
  甘马雷斯: '吉马良斯',
  伊普斯威奇: '伊普斯维奇',
  女王公园巡游者: '女王公园',
  '巴黎圣日耳曼': '巴黎圣曼',
  LASK宁斯: '林茨',
  依云斯堡: '埃尔夫斯堡',
}

/** Reverse index from a common name to every source name that maps to it. */
const ALIASES: Record<string, string[]> = {}
for (const [src, common] of Object.entries(TEAM_COMMON)) (ALIASES[common] ??= []).push(src)

/** Returns the common name for a team, preferring the backend canonical name when one exists. */
export function teamCommon(name: string | null | undefined, canonical?: string | null): string | null {
  const base = (canonical || name)?.trim()
  if (!base) return null
  return TEAM_COMMON[base] ?? base
}

/** Returns the hover text listing the other names of the team, or undefined when it has none. */
export function teamAliasTip(name: string | null | undefined, canonical?: string | null): string | undefined {
  const shown = teamCommon(name, canonical)
  if (!shown) return undefined
  const others = new Set<string>(ALIASES[shown] ?? [])
  if (name && name !== shown) others.add(name)
  if (canonical && canonical !== shown) others.add(canonical)
  return others.size ? `其他名称：${[...others].join('、')}` : undefined
}
