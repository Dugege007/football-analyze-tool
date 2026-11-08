/**
 * 开赛悬停要写明推迟到何时，缺盘格子显示推迟、取消或其他，而不是「无法获取」。
 */
import assert from 'node:assert/strict'
import { kickoffHoverLines, scheduleCellLabel } from '../src/sheet2/scheduleStatus.ts'

const lines = kickoffHoverLines({
  schedule_status: 'postponed',
  schedule_note: '推迟至 2026-11-09 19:00（北京时间）',
  kickoff_actual: '2026-11-09T19:00:00+08:00',
  kickoff_rev: 1,
})
assert.equal(lines[0], '推迟至 2026-11-09 19:00（北京时间）')
assert.equal(lines[1], '开赛时间曾变更（第 1 次）')

const fallback = kickoffHoverLines({
  schedule_status: 'postponed',
  kickoff_actual: '2026-11-09T11:00:00Z',
  kickoff_rev: 2,
})
assert.equal(fallback[0], '推迟至 2026-11-09 19:00（北京时间）')
assert.match(fallback[1], /第 2 次/)

assert.equal(kickoffHoverLines({
  schedule_status: 'other',
  schedule_note: '已从竞彩列表消失，不能确定是推迟、取消还是其他原因',
})[0], '已从竞彩列表消失，不能确定是推迟、取消还是其他原因')

assert.equal(scheduleCellLabel('schedule_postponed'), '推迟')
assert.equal(scheduleCellLabel('schedule_cancelled'), '取消')
assert.equal(scheduleCellLabel('schedule_other'), '其他')
assert.equal(scheduleCellLabel('not_captured'), null)

console.log('schedule status hover ok')
