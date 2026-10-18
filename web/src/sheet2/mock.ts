/**
 * 第 1 期模拟数据：字段名照后端草案。覆盖各种 cell_status、升降（含主让加深、受让方变化）、预警三档、
 * 比赛中、未开始、取消、推迟，以及无临盘退回澳门结算的情况。
 */
import { REAL_BOOKS, type AvgDetail, type AhCell, type Book, type ByBookResult, type FundNumberCell, type OuCell, type Phase, type Phased, type Row, type SettleItem, type X1x2Cell } from './types'
import { emptyFundNumber, emptyFundamentals } from './xg'

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
  /** 为 true 时不写规范名，赛事列按未映射显示待确认星号。 */
  unmappedLeague?: boolean
  home: string
  away: string
  status: Row['match']['status']
  score?: [number, number]
  /** 初、中、临盘，主队让球为正 */
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
  { id: '三001', ko: '2026-10-14T19:00:00+08:00', league: '日职联', home: '鹿岛鹿角', away: '浦和红钻', status: 'finished', score: [2, 0], ahLines: [0.25, 0.5, 0.75], ouLines: [2.25, 2.5, 2.5], dir: '主', alert: 'light' },
  // 受让方变化：主让 0.25 → 平手 → 客让 0.25（跨让方，按深度：降后再升）
  { id: '三002', ko: '2026-10-14T20:30:00+08:00', league: '韩K联', home: '蔚山现代', away: '全北现代', status: 'finished', score: [1, 1], ahLines: [0.25, 0, -0.25], ouLines: [2.5, 2.5, 2.25], dir: '客', alert: 'medium' },
  // 受让方变化：客让 0.5 → 主让 0.25（深度变浅，降盘）
  { id: '三003', ko: '2026-10-14T22:00:00+08:00', league: '英冠', home: '利兹联', away: '谢菲联', status: 'finished', score: [0, 1], ahLines: [-0.5, -0.25, 0.25], ouLines: [2.75, 2.5, 2.5], dir: '主', alert: 'high', noCloseFor: ['bet365'] },
  { id: '三004', ko: '2026-10-14T23:30:00+08:00', league: '德甲', home: '多特蒙德', away: '斯图加特', status: 'live', score: [1, 0], ahLines: [0.75, 0.75, 1], ouLines: [3, 3, 3.25], dir: '主' },
  { id: '三005', ko: '2026-10-15T01:00:00+08:00', league: '西甲', home: '皇家社会', away: '比利亚雷', status: 'not_started', ahLines: [0.25, 0.25, 0.25], ouLines: [2.5, 2.5, 2.5], dir: null, missing: { william: 'pending', bet365: 'pending' } },
  { id: '三006', ko: '2026-10-15T02:45:00+08:00', league: '意甲', home: '那不勒斯', away: '拉齐奥', status: 'not_started', ahLines: [0.5, 0.5, 0.5], ouLines: [2.25, 2.25, 2.25], dir: '客', missing: { crown: 'unavailable', william: 'unavailable', bet365: 'pending' } },
  { id: '三007', ko: '2026-10-15T03:00:00+08:00', league: '法甲', home: '里尔', away: '朗斯', status: 'cancelled', ahLines: [0.25, 0.5, 0.5], ouLines: [2.5, 2.5, 2.5], dir: null },
  { id: '三008', ko: '2026-10-14T21:00:00+08:00', league: '荷甲', home: '阿贾克斯', away: '特温特', status: 'postponed', ahLines: [1, 1, 1], ouLines: [3, 3, 3], dir: null },
  { id: '三009', ko: '2026-10-14T18:00:00+08:00', league: '澳超', home: '悉尼FC', away: '墨尔本城', status: 'finished', score: [3, 1], ahLines: [0.5, 0.75, 0.75], ouLines: [2.75, 3, 3], dir: '主', noCloseFor: ['pinnacle', 'bet365'] },
  { id: '三010', ko: '2026-10-14T17:00:00+08:00', league: '未识别联赛', unmappedLeague: true, home: '待定主队', away: '待定客队', status: 'not_started', ahLines: [0, 0, 0], ouLines: [2.5, 2.5, 2.5], dir: null },
  { id: '二012', ko: '2026-10-13T20:00:00+08:00', league: '世预赛', home: '中国', away: '印尼', status: 'finished', score: [2, 1], ahLines: [0.75, 0.5, 0.25], ouLines: [2.25, 2.25, 2], dir: '主' },
  { id: '日004', ko: '2026-10-11T20:00:00+08:00', league: '挪超', home: '博德闪耀', away: '莫尔德', status: 'finished', score: [2, 2], ahLines: [0.5, 0.5, 0.25], ouLines: [3, 3, 3.25], dir: '主' },
  { id: '六010', ko: '2026-10-10T22:00:00+08:00', league: '瑞超', home: '马尔默', away: '哈马比', status: 'finished', score: [0, 0], ahLines: [0.25, 0.25, 0.25], ouLines: [2.5, 2.5, 2.5], dir: '客' },
  { id: '五003', ko: '2026-10-09T19:30:00+08:00', league: '日职联', home: '川崎前锋', away: '横滨水手', status: 'finished', score: [1, 2], ahLines: [0, -0.25, -0.25], ouLines: [2.75, 2.75, 3], dir: '客' },
  { id: '四001', ko: '2026-10-08T19:00:00+08:00', league: '亚冠', home: '上海海港', away: '光州', status: 'finished', score: [3, 0], ahLines: [0.5, 0.75, 1], ouLines: [2.5, 2.75, 2.75], dir: '主' },
  { id: '一006', ko: '2026-10-12T22:00:00+08:00', league: '葡超', home: '本菲卡', away: '布拉加', status: 'finished', score: [1, 1], ahLines: [0.75, 0.75, 0.75], ouLines: [2.75, 2.75, 2.75], dir: '客' },
]

const BOOK_SHIFT: Record<string, number> = { macau: 0, crown: 0.02, pinnacle: -0.01, william: 0.03, bet365: -0.02 }
const PH: Phase[] = ['open', 'mid', 'close']

function ahSettle(margin: number, side: 'home' | 'away'): 'win' | 'win_half' | 'push' | 'lose_half' | 'lose' {
  // margin = 主队净胜 - line（主让为正）
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
  for (const l of quarterSplit(line)) w += Math.sign(gd - l)
  if (w === 0) return { line, side: null, push: true, cell_status: 'ok' }
  return { line, side: w > 0 ? 'home' : 'away', push: false, cell_status: 'ok' }
}
function ouResult(tg: number, line: number): ByBookResult['ou'] {
  let w = 0
  for (const l of quarterSplit(line)) w += Math.sign(tg - l)
  if (w === 0) return { line, side: null, push: true, cell_status: 'ok' }
  return { line, side: w > 0 ? 'over' : 'under', push: false, cell_status: 'ok' }
}

/** 个别场次个别机构的亚盘盘口（初、中、临），用来演示平票与剔除 */
const AH_OVERRIDE: Record<string, Partial<Record<Book, [number, number, number]>>> = {
  // 临盘 2 家 0.25、2 家 0、1 家 0.5：平票，按中位数取 0.25
  三002: { macau: [0.25, 0, 0.25], crown: [0.25, 0, 0.25], pinnacle: [0.25, 0, 0], william: [0.25, 0, 0], bet365: [0.25, 0, 0.5] },
  // Bet365 临盘 1.75，比多数盘口 1 深 3 档：剔除
  四001: { bet365: [0.5, 0.75, 1.75] },
}

function median(xs: number[]): number {
  const a = [...xs].sort((x, y) => x - y)
  const m = a.length >> 1
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2
}

/** 相对开赛时刻的东八区时间。初盘提前一天，中盘提前 8 小时，临盘提前 1 小时，各家再错开几分钟。 */
const PHASE_LEAD_MIN: Record<Phase, number> = { open: 24 * 60, mid: 8 * 60, close: 60 }
function cnIso(ms: number): string {
  const b = new Date(ms + 8 * 3600e3)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${b.getUTCFullYear()}-${p(b.getUTCMonth() + 1)}-${p(b.getUTCDate())}T${p(b.getUTCHours())}:${p(b.getUTCMinutes())}:00+08:00`
}
function mockSources(books: readonly string[], kickoff: string, phase: Phase): NonNullable<AvgDetail['sources']> {
  const kick = new Date(kickoff).getTime()
  const out: NonNullable<AvgDetail['sources']> = {}
  books.forEach((book, i) => {
    out[book] = {
      // 初盘用开盘数据；临盘、中盘在实时采集与快照之间交替，悬停来源列能看出区别。
      source: phase === 'open' ? '5df_live_opening' : (i % 2 === 0 ? '5df_live' : 'odds_snapshot/actual'),
      recorded_at: Number.isNaN(kick) ? kickoff : cnIso(kick - (PHASE_LEAD_MIN[phase] + i * 5) * 60e3),
    }
  })
  return out
}

/** 亚盘换算用的总进球期望：同一阶段除威廉外大小球盘口的中位数；没有盘口时取 2.6。没有机构参与平均时不写。 */
function attachGoalExpectation(detail: AvgDetail, ouLines: number[]) {
  if (!detail.averaged_books?.length) return
  if (ouLines.length) {
    detail.goal_expectation = median(ouLines)
    detail.goal_expectation_source = 'ou_median_same_phase'
  } else {
    detail.goal_expectation = 2.6
    detail.goal_expectation_source = 'default'
  }
}

function avgLine<C extends { line: number | null; cell_status?: string | null; cell_status_reason?: string | null }>(
  market: 'ah' | 'ou', p: Phase, get: (b: Book) => C | null | undefined, water: (c: C) => [number, number], kickoff: string,
): { ok: boolean; line: number | null; w: [number, number]; detail: AvgDetail; st: object } {
  const have = REAL_BOOKS.filter((b) => get(b)?.cell_status === 'ok') as Book[]
  const missing = REAL_BOOKS.filter((b) => !have.includes(b as Book)).map((b) => ({ book: b as string, reason: get(b)?.cell_status_reason ?? 'not_captured' }))
  const voting = market === 'ou' ? have.filter((b) => b !== 'william') : have
  const lineOf = (b: Book) => get(b)!.line as number
  const cnt = new Map<number, number>()
  voting.forEach((b) => cnt.set(lineOf(b), (cnt.get(lineOf(b)) ?? 0) + 1))
  const top = Math.max(0, ...cnt.values())
  const cands = [...cnt.entries()].filter(([, n]) => n === top).map(([l]) => l)
  let tie: AvgDetail['tie_break'] = 'none'
  let major: number | null = cands[0] ?? null
  let med: number | null = null
  if (cands.length > 1) {
    med = median(voting.map(lineOf))
    const dist = cands.map((l) => Math.abs(l - med!))
    const best = cands.filter((_, i) => dist[i] === Math.min(...dist))
    if (best.length === 1) { tie = 'median'; major = best[0] }
    else if (have.includes('macau')) { tie = 'macau'; major = lineOf('macau') }
    else if (have.includes('pinnacle')) { tie = 'pinnacle'; major = lineOf('pinnacle') }
    else if (have.includes('crown')) { tie = 'crown'; major = lineOf('crown') }
    else if (have.includes('bet365')) { tie = 'bet365'; major = lineOf('bet365') }
    else { tie = 'smaller_line'; major = Math.min(...best) }
  }
  const steps = (b: Book) => (major == null ? 0 : Math.round(Math.abs(lineOf(b) - major) * 4))
  const excluded = have.filter((b) => steps(b) >= 3).map((b) => ({ book: b as string, reason: 'deviation_too_large', steps: steps(b) }))
  const averaged = have.filter((b) => steps(b) < 3)
  const conversions = averaged.map((b) => {
    const ow = water(get(b)!)
    const k = steps(b) * 0.11 * Math.sign(lineOf(b) - (major ?? 0)) * (market === 'ah' ? 1 : -1)
    return { book: b as string, original_line: lineOf(b), steps: steps(b), original_water: ow, converted_water: [+(ow[0] - k).toFixed(2), +(ow[1] + k).toFixed(2)], margin: b === 'william' ? 0.1 : b === 'pinnacle' || b === 'crown' ? 0.03 : 0.05 }
  })
  const detail: AvgDetail = {
    books_used: averaged, books_missing: Object.fromEntries(missing.map((m) => [m.book, m.reason])), majority_line: major,
    converted: conversions.filter((c) => c.steps > 0).map((c) => ({ book: c.book, raw_line: c.original_line, raw: c.original_water, converted: c.converted_water, method: 'power_poisson' })),
    min_books: 3, devig_method: 'power', conversion_model: 'poisson', voting_books: voting, averaged_books: averaged,
    excluded_books: excluded, missing_books: missing, tie_break: tie, median_line: tie === 'none' ? null : med, conversions, averaged_count: averaged.length,
    sources: mockSources(have, kickoff, p),
  }
  if (averaged.length < 2) return { ok: false, line: null, w: [0, 0], detail, st: { cell_status: 'insufficient', cell_status_reason: 'too_few_books', avg_detail: detail } }
  const mean = (i: 0 | 1) => +(conversions.reduce((t, c) => t + c.converted_water[i], 0) / conversions.length).toFixed(2)
  return { ok: true, line: major, w: [mean(0), mean(1)], detail, st: {} }
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
      const line = AH_OVERRIDE[s.id]?.[b]?.[i] ?? s.ahLines[i]
      a[p] = ah(line, hw, 1.86 - hw, { alert: p === 'close' && b === 'macau' ? (s.alert ?? null) : null })
      // 威廉希尔大小球只开半球盘：四分之一盘时往上取到半球盘
      const ouLine = b === 'william' && Math.abs((s.ouLines[i] * 4) % 2) === 1 ? s.ouLines[i] + 0.25 : s.ouLines[i]
      o[p] = ou(ouLine, [0.9, 0.95, 0.99][i] - sh, 1.84 - [0.9, 0.95, 0.99][i] + sh)
      e[p] = eu(+(2.1 - i * 0.12 + sh).toFixed(2), +(3.2 + sh).toFixed(2), +(3.4 + i * 0.2 - sh).toFixed(2))
    })
    if (b === 'bet365' && s.id === '三009') a.open = nilAh(unav('suspect_truncated'))
    ahM[b] = a; ouM[b] = o; euM[b] = e
  }
  // 多家平均（字段照约定第 3 版第 7 节）
  const avgA: Phased<AhCell> = {}
  const avgO: Phased<OuCell> = {}
  const avgE: Phased<X1x2Cell> = {}
  PH.forEach((p) => {
    const ahRes = avgLine('ah', p, (b) => ahM[b]?.[p], (c) => [c.home_water!, c.away_water!], s.ko)
    const ouExpect = REAL_BOOKS.filter((b) => b !== 'william').flatMap((b) => {
      const c = ouM[b]?.[p]
      return c?.cell_status === 'ok' && c.line != null ? [c.line] : []
    })
    attachGoalExpectation(ahRes.detail, ouExpect)
    // 三001 临盘同时带预警和正样本数，用来核对它们与来源明细出现在同一个气泡里。
    const avgAlert = p === 'close' && s.id === '三001' && s.alert ? { alert: s.alert, alert_sample_n: 168 } : {}
    avgA[p] = ahRes.ok ? ah(ahRes.line!, ahRes.w[0], ahRes.w[1], { avg_detail: ahRes.detail, ...avgAlert }) : nilAh(ahRes.st)
    const ouRes = avgLine('ou', p, (b) => ouM[b]?.[p], (c) => [c.over!, c.under!], s.ko)
    avgO[p] = ouRes.ok ? ou(ouRes.line!, ouRes.w[0], ouRes.w[1], { avg_detail: ouRes.detail }) : nilOu(ouRes.st)
    // 欧赔：直接平均，不换算（约定 7.6）
    const used = REAL_BOOKS.filter((b) => euM[b]?.[p]?.cell_status === 'ok') as Book[]
    const missing = REAL_BOOKS.filter((b) => !used.includes(b as Book)).map((b) => ({ book: b, reason: euM[b]?.[p]?.cell_status_reason ?? 'not_captured' }))
    const det: AvgDetail = {
      books_used: used, books_missing: Object.fromEntries(missing.map((m) => [m.book, m.reason])), majority_line: null, converted: null, min_books: 3,
      devig_method: 'power', conversion_model: null, voting_books: [], averaged_books: used, excluded_books: [], missing_books: missing,
      tie_break: 'none', median_line: null, averaged_count: used.length,
      conversions: used.map((b) => { const c = euM[b]![p]!; return { book: b, original_line: null, steps: 0, original_water: [c.home!, c.draw!, c.away!], converted_water: [c.home!, c.draw!, c.away!], margin: 0.05 } }),
      sources: mockSources(used, s.ko, p),
    }
    if (used.length < 2) avgE[p] = nilEu({ cell_status: 'insufficient', cell_status_reason: 'too_few_books', avg_detail: det })
    else {
      const mean = (f: (c: X1x2Cell) => number) => +(used.reduce((t, b) => t + f(euM[b]![p]!), 0) / used.length).toFixed(2)
      avgE[p] = { ...eu(mean((c) => c.home!), mean((c) => c.draw!), mean((c) => c.away!)), avg_detail: det }
    }
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
        item = { code: ahSettle(gd - c.line!, side), line: c.line, water: side === 'home' ? c.home_water : c.away_water, settled_with: useB, fallback_book: useB !== b }
      } else item = { code: 'no_bet', line: null, water: null, settled_with: b, fallback_book: false }
      settle[b] = { ah: item, ou: null, x1x2: null, jc: null }
    }
    const jcLine = s.ahLines[2] >= 0.5 ? -1 : 1
    const jcOut = gd + jcLine > 0 ? 'home' : gd + jcLine < 0 ? 'away' : 'draw'
    result = {
      ...result,
      outcome_1x2: out,
      by_book: byBook,
      jc: { had: { side: out, odds: { home: 1.95, draw: 3.15, away: 3.6 }[out], cell_status: 'ok' }, hhad: { goal_line: jcLine, side: jcOut, odds: { home: 3.9, draw: 3.4, away: 1.72 }[jcOut], cell_status: 'ok' } },
    }
  }
  const jcBase = (i: number) => eu(+(1.95 + i * 0.05).toFixed(2), 3.15, +(3.6 - i * 0.1).toFixed(2))
  const jcLine = s.ahLines[2] >= 0.5 ? -1 : 1
  const jc1: Phased<X1x2Cell> = { open: jcBase(0), mid: jcBase(1), close: s.status === 'not_started' ? nilEu(pend) : jcBase(2) }
  const jch: Row['jc_hhad'] = {
    open: { ...nilEu(unav('no_source')), goal_line: null },
    mid: { ...eu(3.8, 3.4, 1.75), goal_line: jcLine },
    close: s.status === 'not_started' ? { ...nilEu(pend), goal_line: null } : { ...eu(3.9, 3.4, 1.72), goal_line: jcLine },
  }
  return {
    match_id: `${s.ko.slice(0, 10)}|${s.id}`,
    match: {
      jc_id: s.id,
      jingcai_date: jcDate(s),
      kickoff_at: s.ko,
      league: s.league,
      // 模拟赛事名已经是页面上的名称。写入规范名后不再整列标星；未映射的一场留空，用来显示待确认星号。
      league_canonical: s.unmappedLeague ? null : s.league,
      home_team: s.home,
      away_team: s.away,
      status: s.status,
    },
    prediction: s.dir ? { direction: s.dir } : null,
    ah: ahM,
    ou: ouM,
    x1x2: euM,
    jc_1x2: jc1,
    jc_hhad: jch,
    result,
    settlement_by_book: settle,
    fundamentals: emptyFundamentals(),
    _mock: true,
  }
}

const WEEK: Record<string, string> = { 四: '08', 五: '09', 六: '10', 日: '11', 一: '12', 二: '13', 三: '14' }
function jcDate(s: Spec): string {
  const d = WEEK[s.id.slice(0, 1)]
  return `2026-10-${d}`
}

function fundOk(value: number, quality: string | null = 'measured', extra: Partial<FundNumberCell> = {}): FundNumberCell {
  return { ...emptyFundNumber(), value, status: 'ok', quality, ...extra }
}

/** 模拟场只放接口形状的样例，下半场数值是写死的，不是全场减上半场。 */
function withSampleXg(row: Row): Row {
  if (row.match.jc_id === '三001') {
    row.fundamentals = {
      xg_ft_home: fundOk(1.574),
      xg_ft_away: fundOk(0.9),
      xg_ht_home: fundOk(0.726, 'estimated'),
      xg_ht_away: fundOk(0.41),
      xg_2h_home: fundOk(0.91, 'derived'),
      xg_2h_away: fundOk(0.49, 'derived', { reason_zh: '半场统计不完整，由接口标明为推导' }),
    }
  } else if (row.match.jc_id === '三002') {
    const missing = { ...emptyFundNumber(), status: 'unavailable' as const, reason: 'no_bsd_mapping', reason_zh: '没有 BSD 赛事映射' }
    row.fundamentals = { ...emptyFundamentals(), xg_ft_home: missing, xg_ft_away: { ...missing } }
  }
  return row
}

export function mockRows(): Row[] {
  return SPECS.map((spec) => withSampleXg(build(spec)))
}
