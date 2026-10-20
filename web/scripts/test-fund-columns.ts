/**
 * 基本面宽表列：顺序、空值、伤停、人气显示，以及 xG 默认收起。
 * 非 xG 空值是「—」。xG 仍是「-」或「无法获取」。
 * 只出现在「基本面」标签。
 */
import assert from 'node:assert/strict'
import { mapApiRow } from '../src/sheet2/load'
import { injuryCell, goalsText, h2hLast6Text, h2hMatchesText, mapFundStats, popularityText, weatherText } from '../src/sheet2/fundamentals'
import { ALL_SHEET_TABS, buildSheetColumns, columnIds } from '../src/sheet2/tabColumns'
import type { ColDef, ColGroupDef } from 'ag-grid-community'
import type { Row } from '../src/sheet2/types'

const orderBooks = <T,>(def: T[]) => def
const FUND_ORDER = [
  'fund.recent10.home',
  'fund.recent10.away',
  'fund.recent6.home',
  'fund.recent6.away',
  'fund.h2h.n',
  'fund.h2h.last6',
  'fund.xg_ft_home',
  'fund.xg_ft_away',
  'fund.xg_ht_home',
  'fund.xg_ht_away',
  'fund.xg_2h_home',
  'fund.xg_2h_away',
  'fund.rank.home',
  'fund.rank.away',
  'fund.popularity',
  'fund.injury',
  'fund.weather',
]

function fundGroup() {
  const cols = buildSheetColumns({ tab: 'fundamentals', settleBook: 'pinnacle', resultOnly: false, orderBooks })
  const fund = cols.find((col) => 'groupId' in col && col.groupId === 'fund') as ColGroupDef<Row> | undefined
  assert.ok(fund)
  return fund!
}

const fund = fundGroup()
assert.equal(fund.openByDefault, false)
assert.deepEqual(columnIds([fund]), FUND_ORDER)

const byId = new Map<string, ColDef<Row>>()
for (const child of fund.children) {
  if ('colId' in child && child.colId) byId.set(String(child.colId), child)
}
assert.equal(byId.get('fund.xg_ft_home')?.columnGroupShow, undefined)
assert.equal(byId.get('fund.xg_ft_away')?.columnGroupShow, undefined)
for (const id of ['fund.xg_ht_home', 'fund.xg_ht_away', 'fund.xg_2h_home', 'fund.xg_2h_away']) {
  assert.equal(byId.get(id)?.columnGroupShow, 'open', id)
}
assert.deepEqual(fund.children.map((col) => ('headerName' in col ? col.headerName : '')).filter((_, i) => i < 6 || i >= 12), [
  '近10主进失', '近10客进失', '近6主进失', '近6客进失', '交锋场次', '交锋近6',
  '主排名', '客排名', '人气差值', '伤停', '天气',
])

for (const tab of ALL_SHEET_TABS) {
  const ids = columnIds(buildSheetColumns({ tab, settleBook: 'pinnacle', resultOnly: false, orderBooks }))
  const extra = ids.filter((id) => id.startsWith('fund.') && !id.startsWith('fund.xg_'))
  if (tab === 'fundamentals') assert.deepEqual(extra, FUND_ORDER.filter((id) => !id.startsWith('fund.xg_')))
  else assert.deepEqual(extra, [], `${tab} 不应出现非 xG 基本面列`)
  assert.equal(ids.includes('fund.streak'), false)
  assert.equal(ids.includes('fund.support_proxy'), false)
}

assert.equal(goalsText({ gf: 15, ga: 5 }), '15/5')
assert.equal(goalsText({ gf: 0, ga: 0 }), '0/0')
assert.equal(goalsText({ gf: 15, ga: null }), '—')
assert.equal(goalsText(undefined), '—')
assert.equal(h2hMatchesText(6), '6')
assert.equal(h2hMatchesText(0), '0')
assert.equal(h2hMatchesText(null), '—')
assert.equal(h2hLast6Text({ home_gf: 10, home_ga: 8 }), '10 : 8')
assert.equal(h2hLast6Text({ home_gf: 10 }), '—')
assert.equal(popularityText(0.053), '5.3%')
assert.equal(popularityText(null), '—')
assert.equal(weatherText('下雨'), '下雨')
assert.equal(weatherText({ text: '多云' }), '多云')
assert.equal(weatherText(null), '—')
assert.equal(weatherText(''), '—')

assert.deepEqual(injuryCell(null, false), { text: '—', detail: null })
assert.deepEqual(injuryCell([], false), { text: '—', detail: null })
assert.deepEqual(injuryCell({ home: [], away: [] }, false), { text: '—', detail: null })
assert.equal(injuryCell(null, true).text, '无')
assert.equal(injuryCell([], true).text, '无')
assert.equal(JSON.stringify(injuryCell(null, false)).includes('确认无伤停'), false)
assert.equal(injuryCell({ summary: '主队门将缺阵', home: [{ player: '门将甲', status: '伤' }] }, false).text, '主队门将缺阵')
const longInjury = injuryCell({ summary: '主队门将与中锋都缺阵需要轮换' }, false)
assert.equal(longInjury.text.endsWith('…'), true)
assert.equal(longInjury.detail, '主队门将与中锋都缺阵需要轮换')

const row = mapApiRow({
  match_id: '2026-10-10|六001',
  match: { jc_id: '六001', jingcai_date: '2026-10-10', home_team: '主队', away_team: '客队' },
  stats: {
    recent: { last10: { home: { gf: 31, ga: 6 } }, home_streak_last6: 3 },
    h2h: { matches: 4, last6: { home_gf: 5, home_ga: 4 }, home_streak_last6: 1 },
    rank: { home: 5, away: null },
    popularity_diff: 0.053,
    support_proxy_odds: 0.9,
    injury: null,
    weather: '下雨',
    meta: { recent: { source: 'manual_seed', as_of: '2026-10-10T12:00:00+08:00' } },
    obs: { injury: { known_empty: false } },
  },
})
assert.equal(goalsText(row.stats.recent?.last10?.home), '31/6')
assert.equal(goalsText(row.stats.recent?.last10?.away), '—')
assert.equal(row.stats.h2h?.matches, 4)
assert.equal(h2hLast6Text(row.stats.h2h?.last6), '5 : 4')
assert.equal(popularityText(row.stats.popularity_diff), '5.3%')
assert.equal(row.stats.obs?.injury?.known_empty, null)
assert.equal('support_proxy_odds' in row.stats, false)
assert.equal('home_streak_last6' in (row.stats.recent ?? {}), false)
assert.equal(mapFundStats(undefined).popularity_diff, null)

console.log('fund column samples passed')
