/**
 * 样例回归：六009 的竞彩胜平负赛果悬停必须显示接口的 null_reason_zh。
 * 没有中文字段的旧响应仍走现有对照表；对照表里没有的原因码原样显示，页面不另造中文。
 */
import assert from 'node:assert/strict'
import { oddsCellReason, reasonHoverText, resultCellReason } from '../src/sheet2/format'
import { mapApiRow } from '../src/sheet2/load'

const row = mapApiRow({
  match_id: '2026-10-10|六009',
  match: {
    jc_id: '六009',
    jingcai_date: '2026-10-10',
    kickoff_at: '2026-10-10T19:00:00+08:00',
    home_team: '主队',
    away_team: '客队',
    status: 'finished',
  },
  result: {
    status: 'finished',
    home_goals: 2,
    away_goals: 1,
    total_goals: 3,
    jc: {
      had: {
        outcome: null,
        null_reason: 'jc_not_on_sale',
        null_reason_zh: '竞彩未开售胜平负',
        odds: null,
      },
      hhad: {
        outcome: null,
        goal_line: -1,
        null_reason: 'jc_not_on_sale',
        null_reason_zh: '竞彩未开售让球胜平负',
        odds: null,
      },
    },
    by_book: {
      macau: {
        ah: { line: null, side: null, push: false, cell_status: 'unavailable', null_reason_zh: '亚盘赛果没有采集到' },
      },
    },
  },
  ah: {
    macau: {
      open: {
        line: null,
        home_water: null,
        away_water: null,
        cell_status: 'unavailable',
        cell_status_reason: 'not_captured',
        cell_status_reason_zh: '没采到这一格',
        missing_reason: 'book_not_open',
        missing_reason_zh: '机构未开盘',
      },
      mid: {
        line: null,
        home_water: null,
        away_water: null,
        cell_status: 'unavailable',
        cell_status_reason: 'not_captured',
        missing_reason: 'book_not_open',
        missing_reason_zh: '机构未开盘',
      },
      close: {
        cell_status: 'pending',
        cell_status_reason: 'not_due',
      },
    },
  },
  jc_1x2: {
    open: {
      home: null,
      draw: null,
      away: null,
      cell_status: 'unavailable',
      missing_reason: 'jc_not_on_sale',
      missing_reason_zh: '竞彩未开售胜平负',
    },
  },
})

const had = row.result?.jc?.had
const hhad = row.result?.jc?.hhad
assert.equal(had?.side, null)
assert.equal(had?.null_reason, 'jc_not_on_sale')
assert.equal(had?.null_reason_zh, '竞彩未开售胜平负')
assert.equal(had?.cell_status, 'unavailable')
assert.equal(reasonHoverText(resultCellReason(had).reason, resultCellReason(had).reasonZh), '竞彩未开售胜平负')

assert.equal(hhad?.null_reason_zh, '竞彩未开售让球胜平负')
assert.equal(hhad?.goal_line, -1)
assert.equal(reasonHoverText(resultCellReason(hhad).reason, resultCellReason(hhad).reasonZh), '竞彩未开售让球胜平负')

const bookAh = row.result?.by_book?.macau?.ah
assert.equal(reasonHoverText(resultCellReason(bookAh).reason, resultCellReason(bookAh).reasonZh), '亚盘赛果没有采集到')

const open = row.ah.macau?.open
assert.equal(open?.cell_status_reason_zh, '没采到这一格')
assert.equal(open?.missing_reason_zh, '机构未开盘')
const openReason = oddsCellReason(open)
assert.equal(reasonHoverText(openReason.reason, openReason.reasonZh), '没采到这一格')

const midReason = oddsCellReason(row.ah.macau?.mid)
assert.equal(reasonHoverText(midReason.reason, midReason.reasonZh), '机构未开盘')

const closeReason = oddsCellReason(row.ah.macau?.close)
assert.equal(reasonHoverText(closeReason.reason, closeReason.reasonZh), '采集时刻未到')

const jcOpen = oddsCellReason(row.jc_1x2.open)
assert.equal(reasonHoverText(jcOpen.reason, jcOpen.reasonZh), '竞彩未开售胜平负')

const legacy = mapApiRow({
  match_id: 'legacy',
  match: { jc_id: '六001', jingcai_date: '2026-10-10', status: 'finished' },
  result: {
    home_goals: 1,
    away_goals: 0,
    jc: { had: { outcome: null, null_reason: 'jc_not_on_sale' }, hhad: { outcome: 'home', odds: 1.8, goal_line: -1 } },
  },
  ah: { macau: { close: { cell_status: 'unavailable', cell_status_reason: 'not_captured' } } },
})
const legacyHad = legacy.result?.jc?.had
assert.equal(legacyHad?.null_reason, 'jc_not_on_sale')
assert.equal(legacyHad?.null_reason_zh, null)
assert.equal(reasonHoverText(resultCellReason(legacyHad).reason, resultCellReason(legacyHad).reasonZh), 'jc_not_on_sale')
assert.equal(legacy.result?.jc?.hhad?.side, 'home')
assert.equal(reasonHoverText(oddsCellReason(legacy.ah.macau?.close).reason, oddsCellReason(legacy.ah.macau?.close).reasonZh), '没采到')
assert.equal(reasonHoverText('too_few_books', null), '参与平均的机构少于 2 家')
assert.equal(reasonHoverText(null, '  竞彩未开售胜平负  '), '竞彩未开售胜平负')

console.log('reason hover samples passed')
