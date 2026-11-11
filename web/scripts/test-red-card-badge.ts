/**
 * 红牌角标只在该队张数大于等于 1 时出现。
 * 0、null、undefined、缺字段，以及还没有赛果的场次，都不显示。
 * 缺数字段不能被写成 0。主队角标在队名之前，客队角标在队名和星号之后。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createElement as h } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { TeamNameWithRedCard } from '../src/components/RedCardBadge'
import {
  passthroughRedCardCount,
  redCardBadgeAnchor,
  redCardCountOf,
  redCardCountTip,
  visibleRedCardCount,
} from '../src/components/redCardBadge'
import { mapApiRow } from '../src/sheet2/load'

const baseMatch = {
  match_id: '2026-11-01|六001',
  match: {
    jc_id: '六001',
    jingcai_date: '2026-11-01',
    kickoff_at: '2026-11-01T19:35:00+08:00',
    home_team: '曼联',
    away_team: '切尔西',
    status: 'finished',
  },
}

function rowWith(result: Record<string, unknown> | null) {
  return mapApiRow({ ...baseMatch, result })
}

function markup(side: 'home' | 'away', result: { home_red_cards?: number | null; away_red_cards?: number | null } | null, name: string) {
  return renderToStaticMarkup(
    h(
      TeamNameWithRedCard,
      { side, result },
      h('span', { className: 'team-name' }, name, h('span', { className: 'team-pending-mark' }, '*')),
    ),
  )
}

assert.equal(visibleRedCardCount(1), 1)
assert.equal(visibleRedCardCount(2), 2)
assert.equal(visibleRedCardCount(10), 10)
assert.equal(redCardCountTip(1), '红牌 1 张')
assert.equal(redCardCountTip(2), '红牌 2 张')
assert.equal(redCardCountTip(2).includes('权重'), false)
assert.equal(redCardCountTip(2).includes('公式'), false)
assert.equal(redCardBadgeAnchor('home'), 'start')
assert.equal(redCardBadgeAnchor('away'), 'end')

for (const empty of [0, null, undefined, Number.NaN, -1, 1.5, '1', false]) {
  assert.equal(visibleRedCardCount(empty), null)
}

assert.equal(passthroughRedCardCount(undefined), undefined)
assert.equal(passthroughRedCardCount(null), null)
assert.equal(passthroughRedCardCount(0), 0)
assert.equal(passthroughRedCardCount(2), 2)
assert.equal(passthroughRedCardCount('2'), undefined)

const positive = rowWith({
  home_goals: 1,
  away_goals: 0,
  total_goals: 1,
  home_red_cards: 2,
  away_red_cards: 1,
})
assert.equal(positive.result?.home_red_cards, 2)
assert.equal(positive.result?.away_red_cards, 1)
assert.equal(visibleRedCardCount(redCardCountOf(positive.result, 'home')), 2)
assert.equal(visibleRedCardCount(redCardCountOf(positive.result, 'away')), 1)

const zeros = rowWith({
  home_goals: 1,
  away_goals: 1,
  total_goals: 2,
  home_red_cards: 0,
  away_red_cards: 0,
})
assert.equal(zeros.result?.home_red_cards, 0)
assert.equal(zeros.result?.away_red_cards, 0)
assert.equal(visibleRedCardCount(redCardCountOf(zeros.result, 'home')), null)
assert.equal(visibleRedCardCount(redCardCountOf(zeros.result, 'away')), null)

const explicitNull = rowWith({
  home_goals: 2,
  away_goals: 0,
  total_goals: 2,
  home_red_cards: null,
  away_red_cards: null,
})
assert.equal(explicitNull.result?.home_red_cards, null)
assert.equal(explicitNull.result?.away_red_cards, null)
assert.equal(visibleRedCardCount(redCardCountOf(explicitNull.result, 'home')), null)

const missing = rowWith({ home_goals: 1, away_goals: 0, total_goals: 1 })
assert.equal(missing.result?.home_red_cards, undefined)
assert.equal(missing.result?.away_red_cards, undefined)
assert.equal(redCardCountOf(missing.result, 'home'), undefined)
assert.notEqual(redCardCountOf(missing.result, 'away'), 0)
assert.equal(visibleRedCardCount(redCardCountOf(missing.result, 'home')), null)

const unfinished = rowWith(null)
assert.equal(unfinished.result, null)
assert.equal(redCardCountOf(unfinished.result, 'home'), undefined)
assert.equal(redCardCountOf(unfinished.result, 'away'), undefined)
assert.equal(visibleRedCardCount(redCardCountOf(unfinished.result, 'home')), null)
assert.equal(visibleRedCardCount(redCardCountOf(unfinished.result, 'away')), null)

const oneSided = rowWith({
  home_goals: 0,
  away_goals: 1,
  total_goals: 1,
  home_red_cards: 0,
  away_red_cards: 3,
})
assert.equal(visibleRedCardCount(redCardCountOf(oneSided.result, 'home')), null)
assert.equal(visibleRedCardCount(redCardCountOf(oneSided.result, 'away')), 3)

const homeHtml = markup('home', { home_red_cards: 2, away_red_cards: 1 }, '曼联')
assert.equal(homeHtml.includes('aria-label="红牌 2 张"'), true)
assert.equal(homeHtml.includes('红牌 1 张'), false)
assert.ok(homeHtml.indexOf('red-card-badge-home') < homeHtml.indexOf('曼联'))
assert.ok(homeHtml.indexOf('曼联') < homeHtml.indexOf('team-pending-mark'))
assert.equal(homeHtml.includes('team-pending-mark'), true)

const awayHtml = markup('away', { home_red_cards: 2, away_red_cards: 1 }, '切尔西')
assert.equal(awayHtml.includes('aria-label="红牌 1 张"'), true)
assert.equal(awayHtml.includes('红牌 2 张'), false)
assert.ok(awayHtml.indexOf('切尔西') < awayHtml.indexOf('team-pending-mark'))
assert.ok(awayHtml.indexOf('team-pending-mark') < awayHtml.indexOf('red-card-badge-away'))

for (const hidden of [
  markup('home', { home_red_cards: 0, away_red_cards: 2 }, '曼联'),
  markup('away', { home_red_cards: 2, away_red_cards: 0 }, '切尔西'),
  markup('home', { home_red_cards: null, away_red_cards: null }, '曼联'),
  markup('away', { home_red_cards: null, away_red_cards: undefined }, '切尔西'),
  markup('home', {}, '曼联'),
  markup('away', null, '切尔西'),
]) {
  assert.equal(hidden.includes('red-card-badge'), false)
  assert.equal(hidden.includes('team-pending-mark'), true)
}

const sheet = readFileSync('src/sheet2/columns.tsx', 'utf8')
const schedule = readFileSync('src/pages/MatchListPage.tsx', 'utf8')
const detail = readFileSync('src/pages/MatchDetailPage.tsx', 'utf8')
for (const source of [sheet, schedule, detail]) {
  assert.equal(source.includes('TeamNameWithRedCard'), true)
  assert.equal(source.includes('side="home"'), true)
  assert.equal(source.includes('side="away"'), true)
}
assert.equal(detail.includes('<Title level={3}'), true)

console.log('red card badge ok')
