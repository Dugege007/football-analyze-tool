/**
 * 数据表第 1 期：按后端《数据表接口字段约定（第 1 期，草案第 1 版）》定义的字段。
 * 文件：odds-data/ui-feedback/20261014/api-contract-phase1.md。分析师 2026-10-14 已确认字段名不变。
 */

export const BOOKS = ['avg', 'macau', 'crown', 'pinnacle', 'william', 'bet365'] as const
export type Book = (typeof BOOKS)[number]
/** 五家真实机构（多家平均的参与机构，不含竞彩） */
export const REAL_BOOKS = ['macau', 'crown', 'pinnacle', 'william', 'bet365'] as const

export const BOOK_NAME: Record<Book, string> = {
  avg: '多家平均',
  macau: '澳门',
  crown: '皇冠',
  pinnacle: '平博',
  william: '威廉',
  bet365: 'Bet365',
}

export const PHASES = ['close', 'mid', 'open'] as const
export type Phase = (typeof PHASES)[number]
export const PHASE_NAME: Record<Phase, string> = { close: '临盘', mid: '中盘', open: '初盘' }

export type CellStatus = 'ok' | 'pending' | 'unavailable' | 'insufficient'
export type CellStatusReason =
  | 'not_due'
  | 'book_not_open'
  | 'no_source'
  | 'not_captured'
  | 'suspect_truncated'
  | 'too_few_books'
  | string
export type MoveDir = 'up' | 'down' | 'flat' | null
export interface Move {
  compare_to: Phase | null
  line?: MoveDir
  left: MoveDir
  mid?: MoveDir
  right: MoveDir
}
export type AlertLevel = 'light' | 'medium' | 'high' | null

/** 后端可能新增取值（例如 crown、bet365、smaller_line），类型里允许其他字符串 */
export type TieBreak = 'none' | 'median' | 'macau' | 'pinnacle' | 'crown' | 'bet365' | 'smaller_line'
export interface AvgConversion {
  book: string
  original_line: number | null
  steps: number
  /** 亚盘 [主水, 客水]；大小球 [大球水位, 小球水位]；欧赔为胜、平、负三项 */
  original_water: number[]
  converted_water: number[]
  margin: number | null
}
export interface AvgDetail {
  // 第 2 版字段（保留）
  books_used: string[]
  books_missing: Record<string, string>
  majority_line: number | null
  converted: { book: string; raw_line: number | null; raw: number[]; converted: number[]; method: string }[] | null
  min_books: number
  // 第 3 版新增（约定 7.2）
  devig_method?: string | null
  conversion_model?: string | null
  voting_books?: string[]
  averaged_books?: string[]
  excluded_books?: { book: string; reason: string; steps: number }[]
  missing_books?: { book: string; reason: string }[]
  tie_break?: TieBreak | string | null
  median_line?: number | null
  conversions?: AvgConversion[]
  averaged_count?: number | null
}

interface CellCommon {
  cell_status?: CellStatus | null
  cell_status_reason?: CellStatusReason | null
  move?: Move | null
  alert?: AlertLevel
  avg_detail?: AvgDetail | null
}

/** 亚盘格：line 主队让球为正（主让半球 0.5，主队受让半球 -0.5） */
export interface AhCell extends CellCommon {
  line: number | null
  home_water: number | null
  away_water: number | null
}
export interface OuCell extends CellCommon {
  line: number | null
  over: number | null
  under: number | null
}
export interface X1x2Cell extends CellCommon {
  home: number | null
  draw: number | null
  away: number | null
}
export interface JcHhadCell extends X1x2Cell {
  goal_line: number | null
}

export type Phased<T> = Partial<Record<Phase, T | null>>

export type MatchStatus = 'not_started' | 'live' | 'finished' | 'cancelled' | 'postponed'

export interface ByBookResult {
  ah?: { line: number | null; side: 'home' | 'away' | null; push: boolean; cell_status?: CellStatus } | null
  ou?: { line: number | null; side: 'over' | 'under' | null; push: boolean; cell_status?: CellStatus } | null
  x1x2?: { side: 'home' | 'draw' | 'away' | null; odds: number | null; cell_status?: CellStatus } | null
}

export type SettleCode = 'win' | 'win_half' | 'push' | 'lose_half' | 'lose' | 'no_bet'
export interface SettleItem {
  code: SettleCode
  line: number | null
  water: number | null
  settled_with: string
  fallback_book: boolean
}

export interface Row {
  match_id: string
  match: {
    jc_id: string | null
    jingcai_date: string
    kickoff_at: string | null
    league: string | null
    home_team: string | null
    away_team: string | null
    status: MatchStatus | null
  }
  prediction: { direction: string | null; line?: number | null } | null
  ah: Partial<Record<Book, Phased<AhCell>>>
  ou: Partial<Record<Book, Phased<OuCell>>>
  x1x2: Partial<Record<Book, Phased<X1x2Cell>>>
  jc_1x2: Phased<X1x2Cell>
  jc_hhad: Phased<JcHhadCell>
  result: {
    home_goals: number | null
    away_goals: number | null
    total_goals: number | null
    outcome_1x2: 'home' | 'draw' | 'away' | null
    by_book: Partial<Record<Book, ByBookResult>> | null
    jc: {
      had: { side: 'home' | 'draw' | 'away' | null; odds: number | null; cell_status?: CellStatus } | null
      hhad: { goal_line: number | null; side: 'home' | 'draw' | 'away' | null; odds: number | null; cell_status?: CellStatus } | null
    } | null
  } | null
  settlement_by_book: Partial<Record<Book, { ah: SettleItem | null; ou: SettleItem | null; x1x2: SettleItem | null; jc: SettleItem | null } | null>> | null
  /** 前端标记：这一行哪些部分来自模拟数据 */
  _mock?: boolean
}
