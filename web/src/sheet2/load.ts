/**
 * 数据加载：现有批量接口 /table/matches（format=nested）映射到第 1 期字段；接口还没提供的字段留空，页面显示「无法获取」。
 * 不请求 live 快照（include_live=none），不读手工澳门列（ah.macau 已指向 5DollarFootballAPI 来源）。
 */
import { apiBase, type DbSource } from '../api/dataSource'
import { mockRows } from './mock'
import type { AhCell, OuCell, CellStatus, JcHhadCell, MatchStatus, Phase, Phased, Row, X1x2Cell } from './types'

export type DataMode = 'api' | 'mock' | 'both'

type Raw = Record<string, unknown> & { [k: string]: any } // eslint-disable-line @typescript-eslint/no-explicit-any

const PH: Phase[] = ['open', 'mid', 'close']

function stOf(c: Raw | null | undefined, has: boolean): { cell_status: CellStatus; cell_status_reason: string | null } {
  // 欧赔半格等字段不全的格子一律按「无法获取」，不显示单独的主胜（分析师 2026-10-14 定）
  if (c?.cell_status === 'ok' && !has) return { cell_status: 'unavailable', cell_status_reason: 'not_captured' }
  if (c?.cell_status) return { cell_status: c.cell_status, cell_status_reason: c.cell_status_reason ?? null }
  if (has) return { cell_status: 'ok', cell_status_reason: null }
  if (c?.hidden_reason === 'after_as_of') return { cell_status: 'pending', cell_status_reason: 'not_due' }
  return { cell_status: 'unavailable', cell_status_reason: c?.status === 'suspect_truncated' ? 'suspect_truncated' : 'not_captured' }
}

const ALERTS = ['light', 'medium', 'high']
/** 预警：接口给 alert 就用；没给时取现有 fallback_hl_level（分析师 2026-10-14 定） */
function alertOf(c: Raw | null | undefined) {
  // Since backend a837e4a the alert field is authoritative: when the key is present, a null value means no warning.
  // The old fallback_hl_level is used only for responses that do not carry the alert key at all.
  const a = c && 'alert' in c ? c.alert : (c?.fallback_hl_level ?? null)
  return typeof a === 'string' && ALERTS.includes(a) ? (a as 'light' | 'medium' | 'high') : null
}
/** 每格都带上来源、记录时间和多家平均明细（接口没给时为空） */
function extra(c: Raw | null | undefined) {
  return { source: c?.source ?? null, recorded_at: c?.recorded_at ?? null, avg_detail: c?.avg_detail ?? null, alert: alertOf(c),
    // Sample size behind the alert; a new backend field that may be absent, and it can be present when alert is null.
    alert_sample_n: typeof c?.alert_sample_n === 'number' ? (c.alert_sample_n as number) : null,
    move: c?.move ?? null }
}

function mapAh(src: Raw | null | undefined): Phased<AhCell> | undefined {
  if (!src) return undefined
  const out: Phased<AhCell> = {}
  for (const p of PH) {
    const c = src[p] as Raw | null
    const has = c != null && c.line != null && c.home_water != null && c.away_water != null
    // 亚盘 line 主队让球为正（后端确认，见约定第 3 节 ah_line_convention）
    const line = has ? c!.line : null
    out[p] = { line, home_water: has ? c!.home_water : null, away_water: has ? c!.away_water : null, ...stOf(c, has), ...extra(c) }
  }
  return out
}
function mapEu(src: Raw | null | undefined): Phased<X1x2Cell> | undefined {
  if (!src) return undefined
  const out: Phased<X1x2Cell> = {}
  for (const p of PH) {
    const c = src[p] as Raw | null
    const has = c != null && c.home != null && c.draw != null && c.away != null
    out[p] = { home: has ? c!.home : null, draw: has ? c!.draw : null, away: has ? c!.away : null, ...stOf(c, has), ...extra(c) }
  }
  return out
}
function mapOu(src: Raw | null | undefined): Phased<OuCell> | undefined {
  if (!src) return undefined
  const out: Phased<OuCell> = {}
  for (const p of PH) {
    const c = src[p] as Raw | null
    const has = c != null && c.line != null && c.over != null && c.under != null
    out[p] = { line: has ? c!.line : null, over: has ? c!.over : null, under: has ? c!.under : null, ...stOf(c, has), ...extra(c) }
  }
  return out
}
function mapHhad(src: Raw | null | undefined): Phased<JcHhadCell> {
  const eu = mapEu(src) ?? {}
  const out: Phased<JcHhadCell> = {}
  for (const p of PH) out[p] = { ...(eu[p] ?? { home: null, draw: null, away: null, cell_status: 'unavailable' }), goal_line: (src?.[p] as Raw | null)?.goal_line ?? null }
  return out
}

function deriveStatus(it: Raw): MatchStatus | null {
  const m = it.match as Raw
  if (m.status) return m.status
  if (m.postponed) return 'postponed'
  if (it.result) return 'finished'
  const ko = m.kickoff_at ? Date.parse(m.kickoff_at) : NaN
  if (Number.isNaN(ko)) return null
  if (ko > Date.now()) return 'not_started'
  if (Date.now() - ko < 2.5 * 3600e3) return 'live'
  return null
}

export function mapApiRow(it: Raw): Row {
  const m = it.match as Raw
  const ahSrc = (it.ah ?? {}) as Raw
  const euSrc = (it.x1x2 ?? {}) as Raw
  const r = it.result as Raw | null
  return {
    match_id: it.match_id,
    match: {
      jc_id: m.jc_id ?? null,
      jingcai_date: m.jingcai_date,
      kickoff_at: m.kickoff_at ?? null,
      league: m.league ?? null,
      league_canonical: m.league_canonical ?? null,
      home_team_canonical: m.home_team_canonical ?? null,
      away_team_canonical: m.away_team_canonical ?? null,
      home_team: m.home_team ?? null,
      away_team: m.away_team ?? null,
      status: deriveStatus(it),
    },
    prediction: it.prediction ? { direction: it.prediction.direction ?? null } : null,
    ah: {
      // 澳门亚盘：ah.macau 即 5DollarFootballAPI 来源（分析师 2026-10-14 定）；旧手工列 macau_manual 只作存档，页面不读。
      macau: mapAh(ahSrc.macau),
      crown: mapAh(ahSrc.crown),
      pinnacle: mapAh(ahSrc.pinnacle),
      william: mapAh(ahSrc.william),
      bet365: mapAh(ahSrc.bet365),
      avg: mapAh(ahSrc.avg),
    },
    ou: Object.fromEntries(Object.entries((it.ou ?? {}) as Raw).map(([b, v]) => [b, mapOu(v as Raw)])) as Row['ou'],
    x1x2: {
      macau: mapEu(euSrc.macau),
      crown: mapEu(euSrc.crown),
      pinnacle: mapEu(euSrc.pinnacle),
      william: mapEu(euSrc.william),
      bet365: mapEu(euSrc.bet365),
      avg: mapEu(euSrc.avg),
    },
    jc_1x2: mapEu(it.jc_1x2) ?? {},
    jc_hhad: mapHhad(it.jc_hhad),
    result: r
      ? {
          home_goals: r.home_goals ?? null,
          away_goals: r.away_goals ?? null,
          total_goals: r.total_goals ?? (r.home_goals != null && r.away_goals != null ? r.home_goals + r.away_goals : null),
          outcome_1x2: r.outcome_1x2 ?? null,
          by_book: r.by_book ?? null,
          jc: r.jc ?? null,
        }
      : null,
    settlement_by_book: it.settlement_by_book ?? null,
  }
}

/** 接口顶层 books（机构列顺序）；没给时为 null，页面用默认顺序 */
export let apiBooks: string[] | null = null

export async function loadApi(source: DbSource, dateFrom: string, dateTo: string, strategy: string): Promise<Row[]> {
  const rows: Row[] = []
  for (let offset = 0; ; offset += 200) {
    const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, scope: 'jingcai', strategy, include_live: 'none', limit: '200', offset: String(offset) })
    let res: Response
    try {
      res = await fetch(`${apiBase(source)}/table/matches?${q}`)
    } catch {
      throw new Error(`批量接口连接失败，请确认${source === 'replica' ? '副本实例' : '后端实例'}已启动`)
    }
    if (!res.ok) throw new Error(`批量接口返回错误（HTTP ${res.status}）`)
    const body = (await res.json()) as { items?: Raw[]; total?: number; books?: string[] }
    if (offset === 0) apiBooks = Array.isArray(body.books) ? body.books : null
    if (!Array.isArray(body.items)) throw new Error('批量接口响应缺少 items')
    rows.push(...body.items.map(mapApiRow))
    if (typeof body.total !== 'number' || rows.length >= body.total || body.items.length === 0) break
  }
  return rows
}

export async function loadRows(mode: DataMode, source: DbSource, dateFrom: string, dateTo: string, strategy: string): Promise<Row[]> {
  if (mode === 'mock') return mockRows()
  const api = await loadApi(source, dateFrom, dateTo, strategy)
  return mode === 'both' ? [...mockRows(), ...api] : api
}
