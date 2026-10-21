/**
 * 多家平均汇总面板：空态不出现，有有效赔率时出现。
 * 覆盖无法获取、未填写、ASCII 连字符、历史长横、0 家、整格无数值，以及有数值仍出现。
 */
import assert from 'node:assert/strict'
import { showsAvgSummaryPanel, type AvgParticipation } from '../src/sheet2/format'

function books(n: number): AvgParticipation {
  const names = ['macau', 'crown', 'pinnacle', 'william', 'bet365'].slice(0, n)
  return { averaged_count: n, averaged_books: names, books_used: names }
}

assert.equal(
  showsAvgSummaryPanel({ status: 'unavailable', display: '无法获取', value: null, detail: books(0) }),
  false,
  '无法获取',
)
assert.equal(
  showsAvgSummaryPanel({ status: 'insufficient', display: '无法获取', value: null, detail: books(1) }),
  false,
  '家数不足仍显示无法获取',
)
assert.equal(
  showsAvgSummaryPanel({ status: 'pending', display: '未填写', value: null, detail: books(0) }),
  false,
  '未填写',
)
assert.equal(
  showsAvgSummaryPanel({ status: 'unavailable', display: '无法获取', value: null, detail: null }),
  false,
  '缺格子',
)

for (const display of ['-', '—', '–', '−', '－']) {
  assert.equal(
    showsAvgSummaryPanel({ status: 'ok', display, value: 1.2, detail: books(3) }),
    false,
    `横线 ${display}`,
  )
}

assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '0.93', value: null, detail: books(3) }), false, '整格无数值')
assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '0.93', value: Number.NaN, detail: books(3) }), false, '非有限数值')
assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '   ', value: 0.93, detail: books(3) }), false, '空白文案')
assert.equal(
  showsAvgSummaryPanel({
    status: 'ok',
    display: '0.93',
    value: 0.93,
    detail: { averaged_count: 0, averaged_books: ['macau'], books_used: ['macau'] },
  }),
  false,
  '家数为 0',
)
assert.equal(
  showsAvgSummaryPanel({ status: 'ok', display: '0.93', value: 0.93, detail: { averaged_books: [], books_used: [] } }),
  false,
  '参与名单为空',
)
assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '0.93', value: 0.93, detail: null }), false, '没有明细')
assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '0.93', value: 0.93, detail: {} }), false, '明细没有家数')
assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '无法获取', value: 0.93, detail: books(3) }), false, '文案仍是无法获取')
assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '未填写', value: 0.93, detail: books(3) }), false, '文案仍是未填写')

assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '0.93', value: 0.93, detail: books(3) }), true, '水位')
assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '3.75', value: 3.75, detail: books(5) }), true, '欧赔')
assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '-0.5', value: -0.5, detail: books(2) }), true, '受让盘口')
assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '+0.25', value: 0.25, detail: books(2) }), true, '让球盘口')
assert.equal(showsAvgSummaryPanel({ status: 'ok', display: '0', value: 0, detail: books(4) }), true, '平手')
assert.equal(
  showsAvgSummaryPanel({ status: 'ok', display: '0.88', value: 0.88, detail: { books_used: ['macau', 'pinnacle'] } }),
  true,
  '第 2 版只有 books_used',
)

console.log('avg summary panel samples passed')
