/**
 * 数据表快捷日期。
 * 日历日按北京时间计算。北京时间是固定的 UTC+8，与时区名 Etc/GMT-8 相同，没有夏令时。
 * 快捷区间含首尾，并且都不超过 31 个日历日：今日 1 日，本周 7 日，近 7 日 7 日，本月最多 31 日，近 30 日 30 日。
 * 这里只算出起止日期，不改日期面板上哪些天可以点。
 */

/** 数据表日期范围含首尾最多 31 个日历日。五个快捷区间都必须落在这个上限里。 */
export const SHEET_RANGE_MAX_INCLUSIVE_DAYS = 31

export type SheetRangePresetId = 'today' | 'week' | 'last7' | 'month' | 'last30'

export const SHEET_RANGE_PRESETS: readonly { id: SheetRangePresetId; label: string }[] = [
  { id: 'today', label: '今日' },
  { id: 'week', label: '本周' },
  { id: 'last7', label: '近7日' },
  { id: 'month', label: '本月' },
  { id: 'last30', label: '近30日' },
]

export interface SheetYmdRange {
  from: string
  to: string
}

interface CivilDate {
  year: number
  month: number
  day: number
}

const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000
const DAY_MS = 24 * 60 * 60 * 1000

/** 当前 UTC 毫秒。页面在点击快捷按钮时读取，不在渲染过程中直接调用。 */
export function currentInstantMs(): number {
  return Date.now()
}

/** 把某一时刻换成北京时间的公历日。nowMs 是 UTC 毫秒。 */
export function beijingCivilDate(nowMs: number): CivilDate {
  const shifted = new Date(nowMs + BEIJING_OFFSET_MS)
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate(),
  }
}

function utcMidnight(date: CivilDate): number {
  return Date.UTC(date.year, date.month - 1, date.day)
}

function fromUtcMidnight(ms: number): CivilDate {
  const date = new Date(ms)
  return { year: date.getUTCFullYear(), month: date.getUTCMonth() + 1, day: date.getUTCDate() }
}

function addCalendarDays(date: CivilDate, days: number): CivilDate {
  return fromUtcMidnight(utcMidnight(date) + days * DAY_MS)
}

function formatCivil(date: CivilDate): string {
  const month = String(date.month).padStart(2, '0')
  const day = String(date.day).padStart(2, '0')
  return `${date.year}-${month}-${day}`
}

function lastDayOfMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate()
}

/**
 * 周一为一周的第一天。返回值 0 是周一，6 是周日。
 * 周日属于本周的最后一天，不归到下一周。
 */
function daysSinceMonday(date: CivilDate): number {
  const weekday = new Date(utcMidnight(date)).getUTCDay()
  return weekday === 0 ? 6 : weekday - 1
}

/** 含首尾的日历日天数。日期串必须是 YYYY-MM-DD。 */
export function inclusiveCalendarDays(from: string, to: string): number {
  const start = Date.parse(`${from}T00:00:00Z`)
  const end = Date.parse(`${to}T00:00:00Z`)
  return Math.round((end - start) / DAY_MS) + 1
}

/**
 * 算出某个快捷区间的闭区间。
 * 本周是包含今天的周一到周日，跨月、跨年时仍是连续 7 天。
 * 近 7 日是今天往前共 7 天，近 30 日是今天往前共 30 天。
 * 本月是当月 1 日到当月最后一天。
 */
export function sheetRangePreset(id: SheetRangePresetId, nowMs: number): SheetYmdRange {
  const today = beijingCivilDate(nowMs)
  let from = today
  let to = today
  if (id === 'week') {
    from = addCalendarDays(today, -daysSinceMonday(today))
    to = addCalendarDays(from, 6)
  } else if (id === 'last7') {
    from = addCalendarDays(today, -6)
    to = today
  } else if (id === 'month') {
    from = { year: today.year, month: today.month, day: 1 }
    to = { year: today.year, month: today.month, day: lastDayOfMonth(today.year, today.month) }
  } else if (id === 'last30') {
    from = addCalendarDays(today, -29)
    to = today
  }
  return { from: formatCivil(from), to: formatCivil(to) }
}

/** 当前起止日期与哪些快捷区间完全相同。周日或 30 天月份的月末可能同时命中两个区间。 */
export function matchingSheetRangePresets(from: string, to: string, nowMs: number): SheetRangePresetId[] {
  const matched: SheetRangePresetId[] = []
  for (const preset of SHEET_RANGE_PRESETS) {
    const range = sheetRangePreset(preset.id, nowMs)
    if (range.from === from && range.to === to) matched.push(preset.id)
  }
  return matched
}
