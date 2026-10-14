/**
 * League short names for the 赛事 column.
 *
 * The backend returns the full league name in match.league and, for a few leagues only,
 * a canonical name in match.league_canonical. There is no short-name field, so this table
 * maps every known name to the conventional short name used by China Sports Lottery (竞彩)
 * and 500.com. A league that is not in the table falls back to its full name.
 */
const LEAGUE_SHORT: Record<string, string> = {
  英格兰超级联赛: '英超',
  英格兰冠军联赛: '英冠',
  西班牙甲级联赛: '西甲',
  西班牙乙级联赛: '西乙',
  意大利甲级联赛: '意甲',
  意大利乙级联赛: '意乙',
  德国甲级联赛: '德甲',
  德国乙级联赛: '德乙',
  法国甲级联赛: '法甲',
  法国乙级联赛: '法乙',
  荷兰甲级联赛: '荷甲',
  荷兰乙级联赛: '荷乙',
  葡萄牙超级联赛: '葡超',
  比利时甲级联赛: '比甲',
  苏格兰超级联赛: '苏超',
  土耳其超级联赛: '土超',
  芬兰超级联赛: '芬超',
  瑞典超级联赛: '瑞超',
  瑞典超: '瑞超',
  挪威超级联赛: '挪超',
  丹麦超级联赛: '丹超',
  日本J联赛: '日职',
  日职联: '日职',
  日本J2联赛: '日职乙',
  日本J联赛杯: '日联杯',
  韩国K1联赛: '韩K',
  韩K联: '韩K',
  美国职业足球大联盟: '美职',
  澳大利亚超级联赛: '澳超',
  巴西甲级联赛: '巴甲',
  阿根廷甲级联赛: '阿甲',
  沙特阿拉伯职业联赛: '沙特联',
  欧洲冠军联赛: '欧冠',
  欧联杯: '欧联',
  欧足联协会联赛: '欧协联',
  亚足联冠军精英联赛: '亚冠精英',
  亚足联冠军精英联赛二: '亚冠二',
  南美解放者杯: '解放者杯',
  国际友谊: '国际赛',
}

/** Returns the short league name, or the original name when no short name is known. */
export function leagueShort(name: string | null | undefined): string | null {
  if (!name) return null
  return LEAGUE_SHORT[name.trim()] ?? name
}
