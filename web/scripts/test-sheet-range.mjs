// 数据表快捷日期：北京时间日历日，五个区间含首尾且不超过 31 天。
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import { compileWithTypeScript } from './compile-with-tsc.mjs'

const out = mkdtempSync(join(tmpdir(), 'sheet-range-'))
compileWithTypeScript(['src/sheet2/dateRangePresets.ts',
  '--outDir', out, '--module', 'es2022', '--target', 'es2022', '--skipLibCheck', '--ignoreConfig'])
writeFileSync(join(out, 'package.json'), '{"type":"module"}')
const M = await import(pathToFileURL(join(out, 'dateRangePresets.js')).href)

/** 北京时间正午对应的 UTC 毫秒，用来固定“今天”而不受运行环境时区影响。 */
function beijingNoon(isoDate) {
  const [year, month, day] = isoDate.split('-').map(Number)
  return Date.UTC(year, month - 1, day, 4, 0, 0)
}

test('按钮文案与顺序固定为今日、本周、近7日、本月、近30日', () => {
  assert.deepEqual(M.SHEET_RANGE_PRESETS.map((preset) => preset.label), ['今日', '本周', '近7日', '本月', '近30日'])
})

test('2026-10-16 星期五：今日、本周、近7日、本月、近30日', () => {
  const now = beijingNoon('2026-10-16')
  assert.deepEqual(M.sheetRangePreset('today', now), { from: '2026-10-16', to: '2026-10-16' })
  assert.deepEqual(M.sheetRangePreset('week', now), { from: '2026-10-12', to: '2026-10-18' })
  assert.deepEqual(M.sheetRangePreset('last7', now), { from: '2026-10-10', to: '2026-10-16' })
  assert.deepEqual(M.sheetRangePreset('month', now), { from: '2026-10-01', to: '2026-10-31' })
  assert.deepEqual(M.sheetRangePreset('last30', now), { from: '2026-09-17', to: '2026-10-16' })
  assert.equal(M.inclusiveCalendarDays('2026-10-01', '2026-10-31'), 31)
  assert.deepEqual(M.matchingSheetRangePresets('2026-10-10', '2026-10-16', now), ['last7'])
})

test('北京时间跨日以 16:00 UTC 为界', () => {
  const justBefore = Date.parse('2026-10-15T15:59:59.000Z')
  const atMidnight = Date.parse('2026-10-15T16:00:00.000Z')
  assert.deepEqual(M.sheetRangePreset('today', justBefore), { from: '2026-10-15', to: '2026-10-15' })
  assert.deepEqual(M.sheetRangePreset('today', atMidnight), { from: '2026-10-16', to: '2026-10-16' })
  assert.deepEqual(M.beijingCivilDate(atMidnight), { year: 2026, month: 10, day: 16 })
})

test('周日的本周与近7日都是周一到周日，仍然只有 7 天', () => {
  const now = beijingNoon('2026-10-18')
  assert.deepEqual(M.sheetRangePreset('week', now), { from: '2026-10-12', to: '2026-10-18' })
  assert.deepEqual(M.sheetRangePreset('last7', now), { from: '2026-10-12', to: '2026-10-18' })
  assert.deepEqual(M.matchingSheetRangePresets('2026-10-12', '2026-10-18', now), ['week', 'last7'])
})

test('周一的本周向后取到周日，近7日向前取 6 天', () => {
  const now = beijingNoon('2026-10-12')
  assert.deepEqual(M.sheetRangePreset('week', now), { from: '2026-10-12', to: '2026-10-18' })
  assert.deepEqual(M.sheetRangePreset('last7', now), { from: '2026-10-06', to: '2026-10-12' })
})

test('本周跨月仍是 7 天：2026-11-01 周日属于 10 月 26 日那一周', () => {
  const now = beijingNoon('2026-11-01')
  assert.deepEqual(M.sheetRangePreset('week', now), { from: '2026-10-26', to: '2026-11-01' })
  assert.equal(M.inclusiveCalendarDays('2026-10-26', '2026-11-01'), 7)
})

test('年初跨年：2026-01-01 的本周、近7日、近30日回到 2025 年', () => {
  const now = beijingNoon('2026-01-01')
  assert.deepEqual(M.sheetRangePreset('week', now), { from: '2025-12-29', to: '2026-01-04' })
  assert.deepEqual(M.sheetRangePreset('last7', now), { from: '2025-12-26', to: '2026-01-01' })
  assert.deepEqual(M.sheetRangePreset('last30', now), { from: '2025-12-03', to: '2026-01-01' })
  assert.deepEqual(M.sheetRangePreset('month', now), { from: '2026-01-01', to: '2026-01-31' })
})

test('平年二月与闰年二月的本月取到当月最后一天', () => {
  assert.deepEqual(M.sheetRangePreset('month', beijingNoon('2026-02-15')), { from: '2026-02-01', to: '2026-02-28' })
  assert.deepEqual(M.sheetRangePreset('month', beijingNoon('2024-02-15')), { from: '2024-02-01', to: '2024-02-29' })
  assert.equal(M.inclusiveCalendarDays('2024-02-01', '2024-02-29'), 29)
})

test('30 天月份的最后一天，本月与近30日是同一段', () => {
  const now = beijingNoon('2026-04-30')
  assert.deepEqual(M.sheetRangePreset('month', now), { from: '2026-04-01', to: '2026-04-30' })
  assert.deepEqual(M.sheetRangePreset('last30', now), { from: '2026-04-01', to: '2026-04-30' })
  assert.deepEqual(M.matchingSheetRangePresets('2026-04-01', '2026-04-30', now), ['month', 'last30'])
})

test('31 天月份的最后一天，本月是 31 日，近30日从 2 日开始', () => {
  const now = beijingNoon('2026-10-31')
  assert.deepEqual(M.sheetRangePreset('month', now), { from: '2026-10-01', to: '2026-10-31' })
  assert.deepEqual(M.sheetRangePreset('last30', now), { from: '2026-10-02', to: '2026-10-31' })
  assert.equal(M.inclusiveCalendarDays('2026-10-01', '2026-10-31'), 31)
  assert.equal(M.inclusiveCalendarDays('2026-10-02', '2026-10-31'), 30)
})

test('结束日比起始日晚超过 31 天时，截成起始日加 31 天', () => {
  assert.equal(M.SHEET_RANGE_MAX_SPAN_DAYS, 31)
  assert.deepEqual(M.clampSheetYmdRange('2025-10-24', '2026-10-12'), { from: '2025-10-24', to: '2025-11-24' })
  assert.deepEqual(M.clampSheetYmdRange('2025-10-24', '2025-11-25'), { from: '2025-10-24', to: '2025-11-24' })
  assert.equal(M.inclusiveCalendarDays('2025-10-24', '2025-11-24'), 32)
})

test('相差正好 31 天或更短时不改起止', () => {
  assert.deepEqual(M.clampSheetYmdRange('2025-10-24', '2025-11-24'), { from: '2025-10-24', to: '2025-11-24' })
  assert.deepEqual(M.clampSheetYmdRange('2025-10-24', '2025-10-24'), { from: '2025-10-24', to: '2025-10-24' })
  assert.deepEqual(M.clampSheetYmdRange('2025-10-24', '2025-11-01'), { from: '2025-10-24', to: '2025-11-01' })
  assert.deepEqual(M.clampSheetYmdRange('2026-10-01', '2026-10-31'), { from: '2026-10-01', to: '2026-10-31' })
})

test('跨年与闰月按日历日加 31 天', () => {
  assert.deepEqual(M.clampSheetYmdRange('2025-12-15', '2026-06-01'), { from: '2025-12-15', to: '2026-01-15' })
  assert.deepEqual(M.clampSheetYmdRange('2024-01-31', '2024-06-01'), { from: '2024-01-31', to: '2024-03-02' })
  assert.deepEqual(M.clampSheetYmdRange('2025-01-31', '2025-06-01'), { from: '2025-01-31', to: '2025-03-03' })
})

test('结束日不晚于起始日，或日期无法解析时，不改写', () => {
  assert.deepEqual(M.clampSheetYmdRange('2025-11-24', '2025-10-24'), { from: '2025-11-24', to: '2025-10-24' })
  assert.deepEqual(M.clampSheetYmdRange('not-a-date', '2026-01-01'), { from: 'not-a-date', to: '2026-01-01' })
  assert.deepEqual(M.clampSheetYmdRange('2025-02-31', '2026-01-01'), { from: '2025-02-31', to: '2026-01-01' })
})

test('五个快捷区间经过同一截断后仍是原区间', () => {
  const now = beijingNoon('2026-10-31')
  for (const preset of M.SHEET_RANGE_PRESETS) {
    const range = M.sheetRangePreset(preset.id, now)
    assert.deepEqual(M.clampSheetYmdRange(range.from, range.to), range)
    assert.ok(M.inclusiveCalendarDays(range.from, range.to) <= M.SHEET_RANGE_MAX_INCLUSIVE_DAYS)
  }
  assert.deepEqual(M.sheetRangePreset('month', now), { from: '2026-10-01', to: '2026-10-31' })
})

test('2024 年到 2028 年每一天的五个快捷区间都不超过 31 天', () => {
  const cursor = new Date(Date.UTC(2024, 0, 1))
  const end = Date.UTC(2028, 11, 31)
  while (cursor.getTime() <= end) {
    const now = cursor.getTime() + 4 * 60 * 60 * 1000
    for (const preset of M.SHEET_RANGE_PRESETS) {
      const range = M.sheetRangePreset(preset.id, now)
      const days = M.inclusiveCalendarDays(range.from, range.to)
      assert.ok(range.from <= range.to, `${preset.id} ${range.from} > ${range.to}`)
      assert.ok(days >= 1 && days <= M.SHEET_RANGE_MAX_INCLUSIVE_DAYS, `${preset.id} ${range.from}..${range.to} = ${days}`)
    }
    const today = M.sheetRangePreset('today', now)
    assert.equal(M.inclusiveCalendarDays(today.from, today.to), 1)
    const week = M.sheetRangePreset('week', now)
    assert.equal(M.inclusiveCalendarDays(week.from, week.to), 7)
    const last7 = M.sheetRangePreset('last7', now)
    assert.equal(M.inclusiveCalendarDays(last7.from, last7.to), 7)
    assert.equal(last7.to, today.from)
    const last30 = M.sheetRangePreset('last30', now)
    assert.equal(M.inclusiveCalendarDays(last30.from, last30.to), 30)
    assert.equal(last30.to, today.from)
    const month = M.sheetRangePreset('month', now)
    assert.equal(month.from.slice(8), '01')
    assert.equal(month.from.slice(0, 7), today.from.slice(0, 7))
    cursor.setUTCDate(cursor.getUTCDate() + 1)
  }
})
