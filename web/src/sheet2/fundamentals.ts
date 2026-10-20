/**
 * 基本面宽表里除 xG 以外的格子。
 * 空值用「—」。伤停只有 obs.injury.known_empty 为真时才显示「无」，空名单不算已知无伤停。
 * 人气差值只做已有的小数到百分比换算，不在页面上用赔率重算。
 */
import { formatPopularityDiff } from '../api/popularity'
import type { FundGoals, FundMetaEntry, FundStats } from './types'

export const FUND_DASH = '—'

const POP_SOURCE: Record<string, string> = {
  manual_seed: '手工底座',
  close_euro_avg: '多家临盘欧赔',
  close_euro_pinnacle: '平博临盘欧赔',
  close_euro_william: '威廉临盘欧赔',
  close_euro_macau: '澳门临盘欧赔',
  close_euro_crown: '皇冠临盘欧赔',
  close_euro_bet365: 'Bet365临盘欧赔',
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function numText(value: number): string {
  return Number.isInteger(value) ? String(value) : String(value)
}

export function goalsText(side: FundGoals | null | undefined): string {
  const gf = finite(side?.gf)
  const ga = finite(side?.ga)
  if (gf == null || ga == null) return FUND_DASH
  return `${numText(gf)}/${numText(ga)}`
}

export function h2hMatchesText(matches: unknown): string {
  const n = finite(matches)
  return n == null ? FUND_DASH : numText(n)
}

/** 交锋近 6，主队视角，与详情页相同的「进 : 失」。 */
export function h2hLast6Text(last6: { home_gf?: number | null; home_ga?: number | null } | null | undefined): string {
  const gf = finite(last6?.home_gf)
  const ga = finite(last6?.home_ga)
  if (gf == null || ga == null) return FUND_DASH
  return `${numText(gf)} : ${numText(ga)}`
}

export function rankText(value: unknown): string {
  const n = finite(value)
  return n == null ? FUND_DASH : numText(n)
}

export function popularityText(value: unknown): string {
  return formatPopularityDiff(finite(value)) ?? FUND_DASH
}

/** 来源和 as_of。只说明数据从哪来，不写计算规则。 */
export function fundSourceTip(meta: FundMetaEntry | null | undefined): string | null {
  if (!meta) return null
  const bits: string[] = []
  const source = typeof meta.source === 'string' ? meta.source.trim() : ''
  if (source) bits.push(POP_SOURCE[source] ?? source)
  if (typeof meta.as_of === 'string' && meta.as_of.trim()) bits.push(`as_of ${meta.as_of.trim()}`)
  return bits.length ? `数据来源：${bits.join(' · ')}` : null
}

function textOf(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function playerLine(item: unknown): string {
  if (typeof item === 'string') return item.trim()
  if (!item || typeof item !== 'object') return ''
  const row = item as Record<string, unknown>
  const name = textOf(row.player ?? row.name ?? row.player_name)
  if (!name) return ''
  const status = textOf(row.status ?? row.reason)
  return status ? `${name}（${status}）` : name
}

function nameList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map(playerLine).filter(Boolean)
}

const CELL_MAX = 8

function clip(short: string, full: string): { text: string; detail: string | null } {
  const text = short.length <= CELL_MAX ? short : `${short.slice(0, CELL_MAX)}…`
  return { text, detail: full !== text ? full : null }
}

/**
 * 伤停短文案。knownEmpty 为真时显示「无」。
 * 没有名单、空数组、空对象都显示「—」，不写成已确认无伤停。
 */
export function injuryCell(injury: unknown, knownEmpty: boolean): { text: string; detail: string | null } {
  if (knownEmpty) return { text: '无', detail: null }
  if (injury == null) return { text: FUND_DASH, detail: null }
  if (typeof injury === 'string') {
    const text = injury.trim()
    return text ? clip(text, text) : { text: FUND_DASH, detail: null }
  }
  if (Array.isArray(injury)) {
    const names = nameList(injury)
    if (!names.length) return { text: FUND_DASH, detail: null }
    const full = names.join('、')
    return clip(names.length <= 2 ? full : `${names.length}人`, full)
  }
  if (typeof injury !== 'object') return { text: FUND_DASH, detail: null }
  const row = injury as Record<string, unknown>
  const summary = textOf(row.summary ?? row.text)
  const home = nameList(row.home)
  const away = nameList(row.away)
  const names = [...home.map((name) => `主${name}`), ...away.map((name) => `客${name}`)]
  const fullNames = names.join('、')
  if (summary) {
    const full = fullNames && fullNames !== summary ? `${summary}；${fullNames}` : summary
    return clip(summary, full)
  }
  if (!names.length) return { text: FUND_DASH, detail: null }
  const short = names.length <= 2 ? fullNames : `主${home.length}客${away.length}`
  return clip(short, fullNames)
}

export function weatherText(weather: unknown): string {
  if (weather == null) return FUND_DASH
  if (typeof weather === 'string') return weather.trim() || FUND_DASH
  if (typeof weather === 'number' && Number.isFinite(weather)) return String(weather)
  if (typeof weather === 'object') {
    const row = weather as Record<string, unknown>
    for (const key of ['summary', 'text', 'desc', 'condition', 'weather']) {
      const text = textOf(row[key])
      if (text) return text
    }
  }
  return FUND_DASH
}

function sideOf(node: unknown): FundGoals | null {
  if (!node || typeof node !== 'object') return null
  const row = node as Record<string, unknown>
  return { gf: finite(row.gf), ga: finite(row.ga) }
}

function windowOf(recent: unknown, key: 'last10' | 'last6'): { home: FundGoals | null; away: FundGoals | null } | null {
  if (!recent || typeof recent !== 'object') return null
  const block = (recent as Record<string, unknown>)[key]
  if (!block || typeof block !== 'object') return null
  const row = block as Record<string, unknown>
  return { home: sideOf(row.home), away: sideOf(row.away) }
}

function metaOf(raw: unknown): FundStats['meta'] {
  if (!raw || typeof raw !== 'object') return null
  const src = raw as Record<string, unknown>
  const out: NonNullable<FundStats['meta']> = {}
  for (const key of ['recent', 'h2h', 'rank', 'injury', 'weather', 'popularity'] as const) {
    const entry = src[key]
    if (!entry || typeof entry !== 'object') continue
    const row = entry as Record<string, unknown>
    out[key] = {
      source: typeof row.source === 'string' ? row.source : null,
      as_of: typeof row.as_of === 'string' ? row.as_of : null,
    }
  }
  return out
}

/** 只留下宽表要用的字段。走势、支持率代理不进页面行。 */
export function mapFundStats(raw: unknown): FundStats {
  const src = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const h2h = src.h2h && typeof src.h2h === 'object' ? (src.h2h as Record<string, unknown>) : null
  const last6 = h2h?.last6 && typeof h2h.last6 === 'object' ? (h2h.last6 as Record<string, unknown>) : null
  const rank = src.rank && typeof src.rank === 'object' ? (src.rank as Record<string, unknown>) : null
  const obs = src.obs && typeof src.obs === 'object' ? (src.obs as Record<string, unknown>) : null
  const injuryObs = obs?.injury && typeof obs.injury === 'object' ? (obs.injury as Record<string, unknown>) : null
  return {
    recent: {
      last10: windowOf(src.recent, 'last10'),
      last6: windowOf(src.recent, 'last6'),
    },
    h2h: {
      matches: finite(h2h?.matches),
      last6: { home_gf: finite(last6?.home_gf), home_ga: finite(last6?.home_ga) },
    },
    rank: { home: finite(rank?.home), away: finite(rank?.away) },
    popularity_diff: finite(src.popularity_diff),
    injury: src.injury ?? null,
    weather: src.weather ?? null,
    meta: metaOf(src.meta),
    obs: { injury: { known_empty: injuryObs?.known_empty === true ? true : null } },
  }
}

export function emptyFundStats(): FundStats {
  return mapFundStats(undefined)
}
