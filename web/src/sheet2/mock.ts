/**
 * 第 1 期模拟数据：字段名照后端草案。覆盖各种 cell_status、升降（含主让加深、受让方变化）、预警三档、
 * 比赛中、未开始、取消、推迟，以及无临盘退回澳门结算的情况。
 */
import { REAL_BOOKS, type AhCell, type Book, type ByBookResult, type OuCell, type Phase, type Phased, type Row, type SettleItem, type X1x2Cell } from './types'

const pend = { cell_status: 'pending' as const, cell_status_reason: 'not_due' }
const unav = (r = 'not_captured') => ({ cell_status: 'unavailable' as const, cell_status_reason: r })

function ah(line: number, hw: number, aw: number, extra: Partial<AhCell> = {}): AhCell {
  return { line, home_water: hw, away_water: aw, cell_status: 'ok', cell_status_reason: null, alert: null, ...extra }
}
function ou(line: number, o: number, u: number, extra: Partial<OuCell> = {}): OuCell {
  return { line, over: o, under: u, cell_status: 'ok', cell_status_reason: null, alert: null, ...extra }
}
function eu(h: number, d: number, a: number): X1x2Cell {
  return { home: h, draw: d, away: a, cell_status: 'ok', cell_status_reason: null }
}
const nilAh = (s: object): AhCell => ({ line: null, home_water: null, away_water: null, ...s })
const nilOu = (s: object): OuCell => ({ line: null, over: null, under: null, ...s })
const nilEu = (s: object): X1x2Cell => ({ home: null, draw: null, away: null, ...s })

interface Spec {
  id: string
  ko: string
  league: string
  home: string
  away: string
  status: Row['match']['status']
  score?: [number, number]
  /** 草案写法（主让为负）的初、中、临盘 */
  ahLines: [number, number, number]
  ouLines: [number, number, number]
  dir: '主' | '客' | null
  /** 某几家整格缺 */
  missing?: Partial<Record<Book, 'pending' | 'unavailable'>>
  noCloseFor?: Book[]
  alert?: 'light' | 'medium' | 'high'
}

const SPECS: Spec[] = [
  // 主让加深：主让 0.25 → 0.5 → 0.75（升盘，红字）
  { id: '周三001', ko: '2026-10-14T19:00:00+08:00', league: '日职联', home: '鹿岛鹿角', away: '浦和红钻', status: 'finished', score: [2, 0], ahLines: [-0.25, -0.5, -0.75], ouLines: [2.25, 2.5, 2.5], dir: '主', alert: 'light' },
  // 受让方变化：主让 0.25 → 平手 → 客让 0.25（跨让方，按深度：降后再升）
  { id: '周三002', ko: '2026-10-14T20:30:00+08:00', league: '韩K联', home: '蔚山现代', away: '全北现代', status: 'finished', score: [1, 1], ahLines: [-0.25, 0, 0.25], ouLines: [2.5, 2.5, 2.25], dir: '客', alert: 'medium' },
  // 受让方变化：客让 0.5 → 主让 0.25（深度变浅，降盘）
  { id: '周三003', ko: '2026-10-14T22:00:00+08:00', league: '英冠', home: '利兹联', away: '谢菲联', status: 'finished', score: [0, 1], ahLines: [0.5, 0.25, -0.25], ouLines: [2.75, 2.5, 2.5], dir: '主', alert: 'high', noCloseFor: ['bet365'] },
  { id: '周三004', ko: '2026-10-14T23:30:00+08:00', league: '德甲', home: '多特蒙德', away: '斯图加特', status: 'live', score: [1, 0], ahLines: [-0.75, -0.75, -1], ouLines: [3, 3, 3.25], dir: '主' },
  { id: '周三005', ko: '2026-10-15T01:00:00+08:00', league: '西甲', home: '皇家社会', away: '比利亚雷', status: 'not_started', ahLines: [-0.25, -0.25, -0.25], ouLines: [2.5, 2.5, 2.5], dir: null, missing: { william: 'pending', bet365: 'pending' } },
  { id: '周三006', ko: '2026-10-15T02:45:00+08:00', league: '意甲', home: '那不勒斯', away: '拉齐奥', status: 'not_started', ahLines: [-0.5, -0.5, -0.5], ouLines: [2.25, 2.25, 2.25], dir: '客', missing: { crown: 'unavailable', william: 'unavailable', bet365: 'pending' } },
  { id: '周三007', ko: '2026-10-15T03:00:00+08:00', league: '法甲', home: '里尔', away: '朗斯', status: 'cancelled', ahLines: [-0.25, -0.5, -0.5], ouLines: [2.5, 2.5, 2.5], dir: null },
  { id: '周三008', ko: '2026-10-14T21:00:00+08:00', league: '荷甲', home: '阿贾克斯', away: '特温特', status: 'postponed', ahLines: [-1, -1, -1], ouLines: [3, 3, 3], dir: null },
  { id: '周三009', ko: '2026-10-14T18:00:00+08:00', league: '澳超', home: '悉尼FC', away: '墨尔本城', status: 'finished', score: [3, 1], ahLines: [-0.5, -0.75, -0.75], ouLines: [2.75, 3, 3], dir: '主', noCloseFor: ['pinnacle', 'bet365'] },
  { id: '周二012', ko: '2026-10-13T20:00:00+08:00', league: '世预赛', home: '中国', away: '印尼', status: 'finished', score: [2, 1], ahLines: [-0.75, -0.5, -0.25], ouLines: [2.25, 2.25, 2], dir: '主' },
  { id: '周日004', ko: '2026-10-11T20:00:00+08:00', league: '挪超', home: '博德闪耀', away: '莫尔德', status: 'finished', score: [2, 2], ahLines: [-0.5, -0.5, -0.25], ouLines: [3, 3, 3.25], dir: '主' },
  { id: '周六010', ko: '2026-10-10T22:00:00+08:00', league: '瑞超', home: '马尔默', away: '哈马比', status: 'finished', score: [0, 0], ahLines: [-0.25, -0.25, -0.25], ouLines: [2.5, 2.5, 2.5], dir: '客' },
  { id: '周五003', ko: '2026-10-09T19:30:00+08:00', league: '日职联', home: '川崎前锋', away: '横滨水手', status: 'finished', score: [1, 2], ahLines: [0, 0.25, 0.25], ouLines: [2.75, 2.75, 3], dir: '客' },
  { id: '周四001', ko: '2026-10-08T19:00:00+08:00', league: '亚冠', home: '上海海港', away: '光州', status: 'finished', score: [3, 0], ahLines: [-0.5, -0.75, -1], ouLines: [2.5, 2.75, 2.75], dir: '主' },
  { id: '周一006', ko: '2026-10-12T22:00:00+08:00', league: '葡超', home: '本菲卡', away: '布拉加', status: 'finished', score: [1, 1], ahLines: [-0.75, -0.75, -0.75], ouLines: [2.75, 2.75, 2.75], dir: '客' },
]

const BOOK_SHIFT: Record<string, number> = { macau: 0, crown: 0.02, pinnacle: -0.01, william: 0.03, bet365: -0.02 }
const PH: Phase[] = ['open', 'mid', 'close']

function ahSettle(margin: number, side: 'home' | 'away'): 'win' | 'win_half' | 'push' | 'lose_half' | 'lose' {
  // margin = 主队净胜 + 草案 line（主让为负）
  const m = side === 'home' ? margin : -margin
  if (m >= 0.5) return 'win'
  if (m === 0.25) return 'win_half'
  if (m === 0) return 'push'
  if (m === -0.25) return 'lose_half'
  return 'lose'
}
function quarterSplit(x: number): number[] {
  return Math.abs((x * 4) % 2) === 1 ? [x - 0.25, x + 0.25] : [x]
}
function ahResult(gd: number, line: number): ByBookResult['ah'] {
  let w = 0
  for (const l of quarterSplit(line)) w += Math.sign(gd + l)
  if (w === 0) return { line, side: null, push: true, cell_status: 'ok' }
  return { line, side: w > 0 ? 'home' : 'away', push: false, cell_status: 'ok' }
}
function ouResult(tg: number, line: number): ByBookResult['ou'] {
  let w = 0
  for (const l of quarterSplit(line)) w += Math.sign(tg - l)
  if (w === 0) return { line, side: null, push: true, cell_status: 'ok' }
  return { line, side: w > 0 ? 'over' : 'under', push: false, cell_status: 'ok' }
}

function build(s: Spec): Row {
  const ahM: Row['ah'] = {}
  const ouM: Row['ou'] = {}
  const euM: Row['x1x2'] = {}
  for (const b of REAL_BOOKS) {
    const sh = BOOK_SHIFT[b]
    const miss = s.missing?.[b]
    const a: Phased<AhCell> = {}
    const o: Phased<OuCell> = {}
    const e: Phased<X1x2Cell> = {}
    PH.forEach((p, i) => {
      const notYet = s.status === 'not_started' && p === 'close'
      if (miss && p !== 'open') {
        const st = miss === 'pending' ? pend : unav()
        a[p] = nilAh(st); o[p] = nilOu(st); e[p] = nilEu(st)
        return
      }
      if (notYet || (p === 'close' && s.noCloseFor?.includes(b))) {
        const st = notYet ? pend : unav()
        a[p] = nilAh(st); o[p] = nilOu(st); e[p] = nilEu(st)
        return
      }
      const hw = [0.92, 0.97, 0.88][i] + sh
      // 威廉的亚盘临盘与多数机构差一档，用来演示多家平均的换算明细
      const line = b === 'crown' && p === 'close' && s.id === '周三001' ? -1 : s.ahLines[i]
      a[p] = ah(line, hw, 1.86 - hw, { alert: p === 'close' && b === 'macau' ? (s.alert ?? null) : null })
      o[p] = ou(s.ouLines[i], [0.9, 0.95, 0.99][i] - sh, 1.84 - [0.9, 0.95, 0.99][i] + sh)
      e[p] = eu(+(2.1 - i * 0.12 + sh).toFixed(2), +(3.2 + sh).toFixed(2), +(3.4 + i * 0.2 - sh).toFixed(2))
    })
    if (b === 'bet365' && s.id === '周三009') a.open = nilAh(unav('suspect_truncated'))
    ahM[b] = a; ouM[b] = o; euM[b] = e
  }
  // 多家平均
  const avgA: Phased<AhCell> = {}
  const avgO: Phased<OuCell> = {}
  const avgE: Phased<X1x2Cell> = {}
  PH.forEach((p) => {
    const used = REAL_BOOKS.filter((b) => ahM[b]?.[p]?.cell_status === 'ok')
    const missing: Record<string, string> = {}
    REAL_BOOKS.filter((b) => !used.includes(b)).forEach((b) => (missing[b] = ahM[b]?.[p]?.cell_status_reason ?? 'not_captured'))
    if (used.length < 3) {
      const st = { cell_status: 'insufficient' as const, cell_status_reason: 'too_few_books', avg_detail: { books_used: used.slice(), books_missing: missing, majority_line: null, converted: null, min_books: 3 } }
      avgA[p] = nilAh(st); avgO[p] = nilOu(st); avgE[p] = nilEu(st)
      return
    }
    const mean = (f: (b: string) => number) => +(used.reduce((t, b) => t + f(b), 0) / used.length).toFixed(2)
    const lines = used.map((b) => ahM[b as Book]![p]!.line as number)
    const cnt = new Map<number, number>()
    lines.forEach((l) => cnt.set(l, (cnt.get(l) ?? 0) + 1))
    const major = [...cnt.entries()].sort((x, y) => y[1] - x[1])[0][0]
    const conv = used.filter((b) => ahM[b as Book]![p]!.line !== major).map((b) => {
      const c = ahM[b as Book]![p]!
      return { book: b, raw_line: c.line, raw: [c.home_water!, c.away_water!], converted: [+(c.home_water! - 0.22).toFixed(2), +(c.away_water! + 0.22).toFixed(2)], method: '待第 3 期方案定' }
    })
    const detail = { books_used: used.slice(), books_missing: missing, majority_line: major, converted: conv, min_books: 3 }
    avgA[p] = ah(major, mean((b) => ahM[b as Book]![p]!.home_water!), mean((b) => ahM[b as Book]![p]!.away_water!), { avg_detail: detail })
    avgO[p] = ou(ouM.macau![p]!.line!, mean((b) => ouM[b as Book]![p]!.over!), mean((b) => ouM[b as Book]![p]!.under!), { avg_detail: { ...detail, majority_line: ouM.macau![p]!.line, converted: [] } })
    avgE[p] = { ...eu(mean((b) => euM[b as Book]![p]!.home!), mean((b) => euM[b as Book]![p]!.draw!), mean((b) => euM[b as Book]![p]!.away!)), avg_detail: { ...detail, majority_line: null, converted: null } }
  })
  ahM.avg = avgA; ouM.avg = avgO; euM.avg = avgE

  const done = s.status === 'finished' && s.score
  const [hg, ag] = s.score ?? [null, null]
  let result: Row['result'] = { home_goals: hg, away_goals: ag, total_goals: hg != null && ag != null ? hg + ag : null, outcome_1x2: null, by_book: null, jc: null }
  let settle: Row['settlement_by_book'] = null
  if (done && hg != null && ag != null) {
    const gd = hg - ag
    const out = gd > 0 ? 'home' : gd < 0 ? 'away' : 'draw'
    const byBook: Partial<Record<Book, ByBookResult>> = {}
    settle = {}
    const side = s.dir === '主' ? 'home' : s.dir === '客' ? 'away' : null
    for (const b of ['avg', ...REAL_BOOKS] as Book[]) {
      const ca = ahM[b]?.close
      const co = ouM[b]?.close
      const ce = euM[b]?.close
      byBook[b] = {
        ah: ca?.cell_status === 'ok' ? ahResult(gd, ca.line!) : { line: null, side: null, push: false, cell_status: ca?.cell_status ?? 'unavailable' },
        ou: co?.cell_status === 'ok' ? ouResult(hg + ag, co.line!) : { line: null, side: null, push: false, cell_status: co?.cell_status ?? 'unavailable' },
        x1x2: ce?.cell_status === 'ok' ? { side: out, odds: ce[out]!, cell_status: 'ok' } : { side: null, odds: null, cell_status: ce?.cell_status ?? 'unavailable' },
      }
      let item: SettleItem | null = null
      if (side) {
        const useB: Book = b !== 'avg' && ca?.cell_status === 'ok' ? b : 'macau'
        const c = ahM[useB]!.close!
        item = { code: ahSettle(gd + c.line!, side), line: c.line, water: side === 'home' ? c.home_water : c.away_water, settled_with: useB, fallback_book: useB !== b }
      } else item = { code: 'no_bet', line: null, water: null, settled_with: b, fallback_book: false }
      settle[b] = { ah: item, ou: null, x1x2: null, jc: null }
    }
    const jcLine = s.ahLines[2] <= -0.5 ? -1 : 1
    const jcOut = gd + jcLine > 0 ? 'home' : gd + jcLine < 0 ? 'away' : 'draw'
    result = {
      ...result,
      outcome_1x2: out,
      by_book: byBook,
      jc: { had: { side: out, odds: { home: 1.95, draw: 3.15, away: 3.6 }[out], cell_status: 'ok' }, hhad: { goal_line: jcLine, side: jcOut, odds: { home: 3.9, draw: 3.4, away: 1.72 }[jcOut], cell_status: 'ok' } },
    }
  }
  const jcBase = (i: number) => eu(+(1.95 + i * 0.05).toFixed(2), 3.15, +(3.6 - i * 0.1).toFixed(2))
  const jcLine = s.ahLines[2] <= -0.5 ? -1 : 1
  const jc1: Phased<X1x2Cell> = { open: jcBase(0), mid: jcBase(1), close: s.status === 'not_started' ? nilEu(pend) : jcBase(2) }
  const jch: Row['jc_hhad'] = {
    open: { ...nilEu(unav('no_source')), goal_line: null },
    mid: { ...eu(3.8, 3.4, 1.75), goal_line: jcLine },
    close: s.status === 'not_started' ? { ...nilEu(pend), goal_line: null } : { ...eu(3.9, 3.4, 1.72), goal_line: jcLine },
  }
  return {
    match_id: `${s.ko.slice(0, 10)}|${s.id}`,
    match: { jc_id: s.id, jingcai_date: jcDate(s), kickoff_at: s.ko, league: s.league, home_team: s.home, away_team: s.away, status: s.status },
    prediction: s.dir ? { direction: s.dir } : null,
    ah: ahM,
    ou: ouM,
    x1x2: euM,
    jc_1x2: jc1,
    jc_hhad: jch,
    result,
    settlement_by_book: settle,
    _mock: true,
  }
}

const WEEK: Record<string, string> = { 周四: '08', 周五: '09', 周六: '10', 周日: '11', 周一: '12', 周二: '13', 周三: '14' }
function jcDate(s: Spec): string {
  const d = WEEK[s.id.slice(0, 2)]
  return `2026-10-${d}`
}

export function mockRows(): Row[] {
  return SPECS.map(build)
}
