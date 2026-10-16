/**
 * 日历置灰用的纯计算。
 * 「有比赛」只看正式库这一天是否至少有一条比赛记录，不要求赔率、预测或赛果。
 * 月份结果还没回来时保持 unknown，页面不要把加载失败画成整月没有比赛。
 */

export type JingcaiDayMark = 'unknown' | 'has' | 'none'

const MONTH = /^(\d{4})-(\d{2})$/

/** 缓存键是「范围|年-月」，赛程页换范围时不和另一范围的结果混用。 */
export function matchDateCacheKey(scope: string, month: string): string {
  return `${scope}|${month}`
}

/** 某月的闭区间，from、to 都是竞彩日 YYYY-MM-DD。月份不合法时返回 null。 */
export function monthRange(month: string): { from: string; to: string } | null {
  const matched = MONTH.exec(month)
  if (!matched) return null
  const year = Number(matched[1])
  const mon = Number(matched[2])
  if (mon < 1 || mon > 12) return null
  const last = new Date(Date.UTC(year, mon, 0)).getUTCDate()
  const mm = String(mon).padStart(2, '0')
  return {
    from: `${year}-${mm}-01`,
    to: `${year}-${mm}-${String(last).padStart(2, '0')}`,
  }
}

/** 日历上紧挨着的下一个月，跨年时进入一月。 */
export function nextMonth(month: string): string | null {
  const matched = MONTH.exec(month)
  if (!matched) return null
  const year = Number(matched[1])
  const mon = Number(matched[2])
  if (mon < 1 || mon > 12) return null
  if (mon === 12) return `${year + 1}-01`
  return `${year}-${String(mon + 1).padStart(2, '0')}`
}

/**
 * 当前日期面板要请求的月份。
 * 单月选择器只请求正在看的那一个月；范围选择器左右各一个月，所以再带上下一个月。
 */
export function monthsForDatePanel(anchorMonth: string, panelCount: 1 | 2): string[] {
  if (!MONTH.test(anchorMonth)) return []
  if (panelCount === 1) return [anchorMonth]
  const following = nextMonth(anchorMonth)
  return following ? [anchorMonth, following] : [anchorMonth]
}

/**
 * 判断某一天该不该标成无比赛。
 * dates 缺省表示这个月还没有成功返回，调用方应保持普通样式。
 */
export function classifyJingcaiDay(dates: ReadonlySet<string> | undefined, isoDate: string): JingcaiDayMark {
  if (!dates) return 'unknown'
  return dates.has(isoDate) ? 'has' : 'none'
}
