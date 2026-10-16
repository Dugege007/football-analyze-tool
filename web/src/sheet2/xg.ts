/**
 * 基本面 xG 六列。
 * 每一列只读 fundamentals 里对应的外壳，展示时用 toFixed(2) 保留两位小数。
 * 下半场不在前端用全场减去上半场推算。
 */
import { XG_KEYS, type FundNumberCell, type FundStatus, type Fundamentals, type XgKey } from './types'

export const XG_COLUMNS: { key: XgKey; header: string }[] = [
  { key: 'xg_ft_home', header: '全场主队xG' },
  { key: 'xg_ft_away', header: '全场客队xG' },
  { key: 'xg_ht_home', header: '上半场主队xG' },
  { key: 'xg_ht_away', header: '上半场客队xG' },
  { key: 'xg_2h_home', header: '下半场主队xG' },
  { key: 'xg_2h_away', header: '下半场客队xG' },
]

const FUND_STATUS: readonly FundStatus[] = ['ok', 'not_filled', 'unavailable', 'not_defined']

export function emptyFundNumber(): FundNumberCell {
  return {
    value: null,
    status: 'not_filled',
    source: null,
    as_of: null,
    reason: null,
    reason_zh: null,
    quality: null,
    estimated: null,
    derived: null,
  }
}

export function emptyFundamentals(): Fundamentals {
  const out = {} as Fundamentals
  for (const key of XG_KEYS) out[key] = emptyFundNumber()
  return out
}

function textOrNull(v: unknown): string | null {
  if (typeof v !== 'string') return null
  const text = v.trim()
  return text ? text : null
}

function finiteNumber(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/** 只拷贝这一键的外壳。缺键、非对象都记 not_filled，不读取其它键。 */
export function mapFundNumber(raw: unknown): FundNumberCell {
  if (!raw || typeof raw !== 'object') return emptyFundNumber()
  const c = raw as Record<string, unknown>
  const status = FUND_STATUS.includes(c.status as FundStatus) ? (c.status as FundStatus) : 'not_filled'
  return {
    value: finiteNumber(c.value),
    status,
    source: textOrNull(c.source),
    as_of: textOrNull(c.as_of),
    reason: textOrNull(c.reason),
    reason_zh: textOrNull(c.reason_zh),
    quality: textOrNull(c.quality),
    estimated: c.estimated === true ? true : c.estimated === false ? false : null,
    derived: c.derived === true ? true : c.derived === false ? false : null,
  }
}

/** 六键各自映射。某一键缺失时保持 not_filled，不用全场减上半场填下半场。 */
export function mapFundamentals(raw: unknown): Fundamentals {
  const src = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : null
  const out = emptyFundamentals()
  for (const key of XG_KEYS) out[key] = mapFundNumber(src?.[key])
  return out
}

export type XgMark = 'estimated' | 'derived'

export type XgCellView =
  | { kind: 'dash'; text: '-' }
  | { kind: 'unavailable'; text: '无法获取'; reason: string | null; reasonZh: string | null }
  | { kind: 'value'; text: string; mark: XgMark | null; tip: string | null; className: '' | 't-gray' }

/** 展示固定两位小数。不用会去掉末尾 0 的 fmtNum。 */
export function formatXgNumber(value: number): string {
  return value.toFixed(2)
}

function xgMark(cell: FundNumberCell): { mark: XgMark | null; tip: string | null } {
  // 实测不标。quality=measured 优先于旁边的布尔标记。
  if (cell.quality === 'measured') return { mark: null, tip: null }
  if (cell.quality === 'estimated') return { mark: 'estimated', tip: '估算' }
  if (cell.quality === 'derived' || cell.derived === true) {
    return { mark: 'derived', tip: textOrNull(cell.reason_zh) ?? '推导' }
  }
  if (cell.estimated === true) return { mark: 'estimated', tip: '估算' }
  return { mark: null, tip: null }
}

/**
 * 单列展示。
 * ok 且 value 为有限数字时保留两位小数；not_filled、not_defined、缺外壳显示「-」；
 * unavailable 显示「无法获取」，原因原文交给现有悬停，这里不翻译原因码。
 */
export function xgCellView(cell: FundNumberCell | null | undefined): XgCellView {
  if (!cell || (cell.status !== 'unavailable' && cell.status !== 'ok')) return { kind: 'dash', text: '-' }
  if (cell.status === 'unavailable') {
    return {
      kind: 'unavailable',
      text: '无法获取',
      reason: textOrNull(cell.reason),
      reasonZh: textOrNull(cell.reason_zh),
    }
  }
  if (typeof cell.value !== 'number' || !Number.isFinite(cell.value)) return { kind: 'dash', text: '-' }
  const marked = xgMark(cell)
  return {
    kind: 'value',
    text: formatXgNumber(cell.value),
    mark: marked.mark,
    tip: marked.tip,
    className: marked.mark ? 't-gray' : '',
  }
}
