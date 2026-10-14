/**
 * League display names for the 赛事 column.
 *
 * Every display name is the name the user recorded manually in the legacy match records
 * (odds-data/imports/predictions/CFFXDJ_5_V3_predictions.csv and the v2d3 leagues table seeded from them).
 * The link from the backend's full name to that manual name comes from
 * odds-data/research/jingcai-competition-whitelist/competition_alias_seed.csv (canonical_status legacy),
 * which matched every legacy name to the 竞彩 and 5DollarFootballAPI names.
 * Where that file conflicts with the user's decision documents, the decision documents win
 * (docs/schema/v2_0-0316-followup-decisions.md: 巴西甲级联赛 is shown as 巴甲).
 * Updated on 2026-10-14 from the finalised standard approved by 分析师:
 * odds-data/research/name-standard-2026-10-14/competitions.csv (with README.md); every alias in its aliases column maps to its canonical name.
 * Copied by hand into this file on 2026-10-14. When the backend API returns standard names, delete this table and read the API field instead.
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
  "巴西甲": '巴甲',
  "巴西甲级联赛": '巴甲',
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

/** Returns the manual league name, or the source name when there is no manual record. */
export function leagueShort(name: string | null | undefined): string | null {
  if (!name) return null
  return LEAGUE_DISPLAY[name.trim()] ?? name
}
