/**
 * 基本面 xG 六列：只读外壳，展示保留两位小数。
 * not_filled、缺键显示「-」；unavailable 显示「无法获取」。
 * estimated、derived 用灰字标记。下半场不由全场减上半场。
 * xG 只出现在「基本面」标签，预测和机构标签没有这六列。
 */
import assert from 'node:assert/strict'
import { reasonHoverText } from '../src/sheet2/format'
import { mapApiRow } from '../src/sheet2/load'
import { COLUMN_WIDTHS } from '../src/sheet2/columnWidths'
import { XG_KEYS } from '../src/sheet2/types'
import { ALL_SHEET_TABS, BOOK_TABS, OVERVIEW_TABS, buildSheetColumns, columnIds, type SheetTabId } from '../src/sheet2/tabColumns'
import { emptyFundNumber, formatXgNumber, mapFundamentals, xgCellView } from '../src/sheet2/xg'

assert.equal(formatXgNumber(1.574), '1.57')
assert.equal(formatXgNumber(1.5), '1.50')
assert.equal(formatXgNumber(1.236), (1.236).toFixed(2))
assert.equal(formatXgNumber(2), '2.00')
assert.equal(formatXgNumber(1.574), (1.574).toFixed(2))

const ok = xgCellView({ ...emptyFundNumber(), status: 'ok', value: 1.56789, quality: 'measured' })
assert.equal(ok.kind, 'value')
if (ok.kind === 'value') {
  assert.equal(ok.text, '1.57')
  assert.equal(ok.mark, null)
  assert.equal(ok.className, '')
  assert.equal(ok.text.includes('1.567'), false)
}

assert.deepEqual(xgCellView({ ...emptyFundNumber(), status: 'not_filled', value: null }), { kind: 'dash', text: '-' })
assert.deepEqual(xgCellView({ ...emptyFundNumber(), status: 'not_defined', value: null }), { kind: 'dash', text: '-' })
assert.deepEqual(xgCellView(undefined), { kind: 'dash', text: '-' })
assert.deepEqual(xgCellView(null), { kind: 'dash', text: '-' })
assert.deepEqual(xgCellView({ ...emptyFundNumber(), status: 'ok', value: null }), { kind: 'dash', text: '-' })

const unavailable = xgCellView({
  ...emptyFundNumber(),
  status: 'unavailable',
  value: null,
  reason: 'no_bsd_mapping',
  reason_zh: '没有 BSD 赛事映射',
})
assert.equal(unavailable.kind, 'unavailable')
if (unavailable.kind === 'unavailable') {
  assert.equal(unavailable.text, '无法获取')
  assert.equal(reasonHoverText(unavailable.reason, unavailable.reasonZh), '没有 BSD 赛事映射')
}

const estimated = xgCellView({ ...emptyFundNumber(), status: 'ok', value: 0.726, quality: 'estimated' })
assert.equal(estimated.kind, 'value')
if (estimated.kind === 'value') {
  assert.equal(estimated.text, '0.73')
  assert.equal(estimated.mark, 'estimated')
  assert.equal(estimated.tip, '估算')
  assert.equal(estimated.className, 't-gray')
}

const estimatedFlag = xgCellView({ ...emptyFundNumber(), status: 'ok', value: 1.2, estimated: true })
assert.equal(estimatedFlag.kind, 'value')
if (estimatedFlag.kind === 'value') {
  assert.equal(estimatedFlag.text, '1.20')
  assert.equal(estimatedFlag.mark, 'estimated')
  assert.equal(estimatedFlag.className, 't-gray')
}

const measuredFlag = xgCellView({ ...emptyFundNumber(), status: 'ok', value: 1.2, quality: 'measured', estimated: true })
assert.equal(measuredFlag.kind, 'value')
if (measuredFlag.kind === 'value') assert.equal(measuredFlag.mark, null)

const derived = xgCellView({ ...emptyFundNumber(), status: 'ok', value: 0.905, quality: 'derived' })
assert.equal(derived.kind, 'value')
if (derived.kind === 'value') {
  assert.equal(derived.text, '0.91')
  assert.equal(derived.mark, 'derived')
  assert.equal(derived.tip, '推导')
  assert.equal(derived.className, 't-gray')
}

const derivedZh = xgCellView({
  ...emptyFundNumber(),
  status: 'ok',
  value: 0.49,
  derived: true,
  reason_zh: '半场统计不完整，由接口标明为推导',
})
assert.equal(derivedZh.kind, 'value')
if (derivedZh.kind === 'value') {
  assert.equal(derivedZh.text, '0.49')
  assert.equal(derivedZh.mark, 'derived')
  assert.equal(derivedZh.tip, '半场统计不完整，由接口标明为推导')
}

const blank = mapFundamentals(undefined)
for (const key of XG_KEYS) {
  assert.equal(blank[key].status, 'not_filled')
  assert.equal(blank[key].value, null)
  assert.equal(xgCellView(blank[key]).kind, 'dash')
}
assert.equal(xgCellView(mapFundamentals({ xg_ft_home: { value: 1, status: 'ok' } }).xg_ft_away).kind, 'dash')

const base = {
  match_id: '2026-10-10|六001',
  match: {
    jc_id: '六001',
    jingcai_date: '2026-10-10',
    kickoff_at: '2026-10-10T19:00:00+08:00',
    home_team: '主队',
    away_team: '客队',
    status: 'finished',
  },
}

const missingRow = mapApiRow({ ...base })
assert.equal(missingRow.fundamentals.xg_2h_home.value, null)
assert.equal(xgCellView(missingRow.fundamentals.xg_2h_home).text, '-')

const longRow = mapApiRow({
  ...base,
  fundamentals: {
    xg_ft_home: { value: 1.574, status: 'ok', quality: 'measured' },
    xg_ht_home: { value: 0.7, status: 'ok', quality: 'measured' },
    xg_2h_home: { value: 0.905, status: 'ok', quality: 'derived' },
  },
})
assert.equal(longRow.fundamentals.xg_ft_home.value, 1.574)
assert.equal(xgCellView(longRow.fundamentals.xg_ft_home).kind === 'value' && xgCellView(longRow.fundamentals.xg_ft_home).text, '1.57')
assert.equal(longRow.fundamentals.xg_2h_home.value, 0.905)
assert.notEqual(longRow.fundamentals.xg_2h_home.value, 1.574 - 0.7)

const noSecondHalf = mapApiRow({
  ...base,
  fundamentals: {
    xg_ft_home: { value: 1.8, status: 'ok', quality: 'measured' },
    xg_ht_home: { value: 0.7, status: 'ok', quality: 'measured' },
  },
})
assert.equal(noSecondHalf.fundamentals.xg_2h_home.status, 'not_filled')
assert.equal(noSecondHalf.fundamentals.xg_2h_home.value, null)
assert.equal(xgCellView(noSecondHalf.fundamentals.xg_2h_home).text, '-')
assert.notEqual(noSecondHalf.fundamentals.xg_2h_home.value, 1.1)

const textValue = mapFundamentals({ xg_ft_home: { value: '1.23456', status: 'ok' } })
assert.equal(textValue.xg_ft_home.value, null)
assert.equal(xgCellView(textValue.xg_ft_home).text, '-')

assert.equal(COLUMN_WIDTHS['fund.xg'], 60)
assert.deepEqual(OVERVIEW_TABS.map((t) => t.label), ['预测', '基本面', '亚盘对照', '大小对照', '欧赔对照'])
assert.deepEqual(BOOK_TABS.map((t) => t.label), ['多家平均', '澳门', '皇冠', '平博', '威廉', 'Bet365', '竞彩'])

const xgIds = XG_KEYS.map((key) => `fund.${key}`)
const orderBooks = <T,>(def: T[]) => def
for (const tab of ALL_SHEET_TABS) {
  const ids = columnIds(buildSheetColumns({ tab, settleBook: 'pinnacle', resultOnly: false, orderBooks }))
  const found = ids.filter((id) => id.startsWith('fund.xg_'))
  if (tab === 'fundamentals') {
    assert.deepEqual(found, xgIds)
    const fund = buildSheetColumns({ tab, settleBook: 'pinnacle', resultOnly: false, orderBooks }).find((col) => 'groupId' in col && col.groupId === 'fund')
    assert.ok(fund && 'children' in fund)
    if (fund && 'children' in fund) {
      for (const child of fund.children) {
        if ('colId' in child && String(child.colId).startsWith('fund.xg_')) {
          assert.equal(child.width, 60)
        }
      }
    }
  } else {
    assert.deepEqual(found, [], `${tab} 不应出现 xG 列`)
  }
}

const predictIds = columnIds(buildSheetColumns({ tab: 'predict' as SheetTabId, settleBook: 'pinnacle', resultOnly: false, orderBooks }))
assert.equal(predictIds.includes('pred.ah'), true)
assert.equal(predictIds.some((id) => id.startsWith('fund.')), false)

const fundIds = columnIds(buildSheetColumns({ tab: 'fundamentals', settleBook: 'macau', resultOnly: false, orderBooks }))
assert.equal(fundIds.includes('pred.ah'), true)
assert.equal(fundIds.includes('jcDay'), true)

console.log('xg column samples passed')
