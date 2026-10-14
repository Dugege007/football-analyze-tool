import { formatHandicap } from '../sheet/handicap'
import type { AhCell, CellStatus, MoveDir, Move, OuCell, Phase, Phased, X1x2Cell } from './types'

/** 亚盘 line 主队让球为正（与现有接口、formatHandicap 一致）；显示时让球方带 +。 */
export const ahLineText = (line: number | null | undefined): string => formatHandicap(line)

export const STATUS_TEXT: Record<Exclude<CellStatus, 'ok'>, string> = {
  pending: '未填写',
  unavailable: '无法获取',
  insufficient: '数据不足',
}
export const REASON_TEXT: Record<string, string> = {
  not_due: '采集时刻未到',
  book_not_open: '机构未开盘',
  no_source: '没有来源',
  not_captured: '没采到',
  suspect_truncated: '初盘疑似截断',
  deviation_too_large: '偏离多数盘口 3 档或以上',
  conversion_failed: '模型算不出换算结果',
  too_few_books: '有数据的机构少于 3 家',
}

export const fmtNum = (v: number | null | undefined): string => (v == null ? '-' : String(Number(v.toFixed(2))))

/** 格子状态：接口没给 cell_status 时一律按「无法获取」（草案第 8 节）。 */
export function statusOf(c: { cell_status?: CellStatus | null; avg_detail?: { averaged_count?: number | null } | null } | null | undefined): CellStatus {
  if (!c || !c.cell_status) return 'unavailable'
  // 多家平均：参与平均的家数少于 3 时一律显示「数据不足」（约定 7.4）
  const n = c.avg_detail?.averaged_count
  if (c.cell_status === 'ok' && n != null && n < 3) return 'insufficient'
  return c.cell_status
}

export function waterDir(prev: number | null | undefined, cur: number | null | undefined): MoveDir {
  if (prev == null || cur == null) return null
  if (Math.abs(cur - prev) < 1e-9) return 'flat'
  return cur > prev ? 'up' : 'down'
}

/** 亚盘升降：按让球方实际让球深度（让得更多为升），跨让方也按深度比较；不看数值正负。 */
export function ahLineDir(prev: number | null | undefined, cur: number | null | undefined): MoveDir {
  if (prev == null || cur == null) return null
  const d = Math.abs(cur) - Math.abs(prev)
  if (Math.abs(d) < 1e-9) return 'flat'
  return d > 0 ? 'up' : 'down'
}
export function ouLineDir(prev: number | null | undefined, cur: number | null | undefined): MoveDir {
  return waterDir(prev, cur)
}

function prevPhase<T extends object>(cells: Phased<T> | undefined, phase: Phase): { p: Phase; c: T } | null {
  if (!cells || phase === 'open') return null
  const ok = (x: T | null | undefined) => x && statusOf(x as { cell_status?: CellStatus }) === 'ok'
  if (phase === 'close' && ok(cells.mid)) return { p: 'mid', c: cells.mid as T }
  if (ok(cells.open)) return { p: 'open', c: cells.open as T }
  return null
}

/** 接口给了 move 就用；没给时前端按同一口径自己算（中盘比初盘，临盘比中盘，中盘缺时比初盘）。 */
export function ahMove(cells: Phased<AhCell> | undefined, phase: Phase): Move | null {
  const c = cells?.[phase]
  if (!c || phase === 'open') return null
  if (c.move) return c.move
  const pv = prevPhase(cells, phase)
  if (!pv) return null
  const ld = ahLineDir(pv.c.line, c.line)
  // 换盘时水位不比较，只给盘口上色（分析师 2026-10-14 定）
  const nc = ld === 'up' || ld === 'down' || (pv.c.line != null && c.line != null && pv.c.line !== c.line)
  return { compare_to: pv.p, line: ld, left: nc ? 'not_comparable' : waterDir(pv.c.home_water, c.home_water), right: nc ? 'not_comparable' : waterDir(pv.c.away_water, c.away_water) }
}
export function ouMove(cells: Phased<OuCell> | undefined, phase: Phase): Move | null {
  const c = cells?.[phase]
  if (!c || phase === 'open') return null
  if (c.move) return c.move
  const pv = prevPhase(cells, phase)
  if (!pv) return null
  const ld = ouLineDir(pv.c.line, c.line)
  const nc = ld === 'up' || ld === 'down'
  return { compare_to: pv.p, line: ld, left: nc ? 'not_comparable' : waterDir(pv.c.over, c.over), right: nc ? 'not_comparable' : waterDir(pv.c.under, c.under) }
}
export function x1x2Move(cells: Phased<X1x2Cell> | undefined, phase: Phase): Move | null {
  const c = cells?.[phase]
  if (!c || phase === 'open') return null
  if (c.move) return c.move
  const pv = prevPhase(cells, phase)
  if (!pv) return null
  return { compare_to: pv.p, line: null, left: waterDir(pv.c.home, c.home), mid: waterDir(pv.c.draw, c.draw), right: waterDir(pv.c.away, c.away) }
}

export const SETTLE_TEXT = { win: '赢', win_half: '赢半', push: '走水', lose_half: '输半', lose: '输', no_bet: '-' } as const

/** 带时区的时间显示成东八区「月-日 时:分」；没有时区按原样 */
export function bjTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const b = new Date(d.getTime() + 8 * 3600e3)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(b.getUTCMonth() + 1)}-${p(b.getUTCDate())} ${p(b.getUTCHours())}:${p(b.getUTCMinutes())}`
}
export const SOURCE_TEXT: Record<string, string> = {
  '5df_live_opening': '5DollarFootballAPI 开盘数据',
  '5df_live': '5DollarFootballAPI 实时采集',
  '5df_hist_asof_full': '5DollarFootballAPI 历史补数',
}
export const sourceText = (s: string | null | undefined): string => (s ? (SOURCE_TEXT[s] ?? (s.startsWith('5df') ? `5DollarFootballAPI（${s}）` : s)) : '未知')
