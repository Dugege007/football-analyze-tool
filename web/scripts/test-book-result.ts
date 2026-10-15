/**
 * 机构赛果列只读 result.by_book。
 * 整段为空、格子缺失、状态为空或 pending 时显示「-」。
 * 只有接口标明无法获取时才走「无法获取」，并保留中文悬停原因。
 * 有 display_text 时直接用短文案，不用盘口另算输赢。
 */
import assert from 'node:assert/strict'
import { bookMarketResultView, reasonHoverText } from '../src/sheet2/format'
import { mapApiRow } from '../src/sheet2/load'

const finished = {
  match_id: '2026-10-10|六001',
  match: {
    jc_id: '六001',
    jingcai_date: '2026-10-10',
    kickoff_at: '2026-10-10T19:00:00+08:00',
    home_team: '主队',
    away_team: '客队',
    status: 'finished',
  },
  ah: {
    macau: {
      close: { line: 0.75, home_water: 0.9, away_water: 0.9, cell_status: 'ok' },
    },
  },
}

const hidden = mapApiRow({
  ...finished,
  result: { status: 'finished', home_goals: 2, away_goals: 1, total_goals: 3, outcome_1x2: 'home', by_book: null },
})
assert.equal(hidden.result?.by_book, null)
assert.equal(bookMarketResultView(hidden.result?.by_book, 'macau', 'ah').kind, 'dash')
assert.equal(bookMarketResultView(hidden.result?.by_book, 'macau', 'ou').kind, 'dash')
assert.equal(bookMarketResultView(hidden.result?.by_book, 'macau', 'x1x2').kind, 'dash')

const row = mapApiRow({
  ...finished,
  result: {
    status: 'finished',
    home_goals: 2,
    away_goals: 1,
    total_goals: 3,
    outcome_1x2: 'home',
    by_book: {
      macau: {
        ah: {
          // 盘口故意写成 9.75。短文案仍必须是接口的「主+0.25」，不能按这个盘口重算。
          line: 9.75,
          side: 'home',
          push: false,
          settle_code: 'win',
          display_text: '主+0.25',
          cell_status: 'ok',
        },
        ou: {
          line: 2.25,
          side: 'over',
          push: false,
          settle_code: 'win',
          display_text: '2.25大',
          cell_status: 'ok',
        },
        x1x2: {
          side: 'home',
          odds: 1.11,
          display_text: '胜 1.11',
          cell_status: 'ok',
          settle_code: null,
        },
      },
      crown: {
        ah: {
          line: null,
          side: null,
          push: false,
          settle_code: null,
          display_text: null,
          cell_status: 'unavailable',
          null_reason: 'not_captured',
          null_reason_zh: '时刻已过但没有采到，补数也没补到',
        },
        ou: {
          line: null,
          side: null,
          push: false,
          cell_status: 'pending',
          null_reason: 'not_due',
          null_reason_zh: '采集时刻未到',
        },
        x1x2: {
          side: null,
          odds: null,
          display_text: null,
          cell_status: null,
          push: false,
        },
      },
      pinnacle: {
        ah: { line: 0, side: null, push: true, settle_code: 'push', cell_status: 'ok' },
        ou: { line: 2.5, side: 'under', push: false, cell_status: 'ok' },
        x1x2: { side: 'draw', odds: 3.2, cell_status: 'ok' },
      },
    },
  },
})

const macauAh = row.result?.by_book?.macau?.ah
assert.equal(macauAh?.settle_code, 'win')
assert.equal(macauAh?.display_text, '主+0.25')
assert.equal(macauAh?.cell_status, 'ok')
const ahView = bookMarketResultView(row.result?.by_book, 'macau', 'ah')
assert.deepEqual(ahView, { kind: 'text', text: '主+0.25', tone: 'orange' })

const ouView = bookMarketResultView(row.result?.by_book, 'macau', 'ou')
assert.deepEqual(ouView, { kind: 'text', text: '2.25大', tone: 'orange' })

const euView = bookMarketResultView(row.result?.by_book, 'macau', 'x1x2')
assert.deepEqual(euView, { kind: 'text', text: '胜 1.11', tone: 'orange' })

const missing = bookMarketResultView(row.result?.by_book, 'crown', 'ah')
assert.equal(missing.kind, 'status')
if (missing.kind === 'status') {
  assert.equal(missing.status, 'unavailable')
  assert.equal(reasonHoverText(missing.reason, missing.reasonZh), '时刻已过但没有采到，补数也没补到')
}

assert.equal(bookMarketResultView(row.result?.by_book, 'crown', 'ou').kind, 'dash')
assert.equal(bookMarketResultView(row.result?.by_book, 'crown', 'x1x2').kind, 'dash')
assert.equal(bookMarketResultView(row.result?.by_book, 'avg', 'ah').kind, 'dash')

const pushView = bookMarketResultView(row.result?.by_book, 'pinnacle', 'ah')
assert.deepEqual(pushView, { kind: 'text', text: '走水', tone: 'plain' })

const underView = bookMarketResultView(row.result?.by_book, 'pinnacle', 'ou')
assert.deepEqual(underView, { kind: 'text', text: '2.5小', tone: 'blue' })

const drawView = bookMarketResultView(row.result?.by_book, 'pinnacle', 'x1x2')
assert.deepEqual(drawView, { kind: 'text', text: '平 3.2', tone: 'plain' })

const away = bookMarketResultView(
  { william: { ah: { line: 0.25, side: 'away', push: false, display_text: '客-0.25', cell_status: 'ok', settle_code: 'lose_half' } } },
  'william',
  'ah',
)
assert.deepEqual(away, { kind: 'text', text: '客-0.25', tone: 'blue' })

console.log('book result samples passed')
