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
/** not_comparable：换盘时水位不比较（只给盘口上色） */
export type MoveDir = 'up' | 'down' | 'flat' | 'not_comparable' | null
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
  excluded_books?: { book: string; reason: string; steps: number; original_line?: number | null; original_water?: number[] | null; margin?: number | null }[]
  missing_books?: { book: string; reason: string; reason_zh?: string | null; missing_reason_zh?: string | null }[]
  tie_break?: TieBreak | string | null
  median_line?: number | null
  conversions?: AvgConversion[]
  averaged_count?: number | null
  // 7.7 新增
  goal_expectation?: number | null
  goal_expectation_source?: string | null
  sources?: Record<string, { source: string | null; recorded_at: string | null }> | null
}

interface CellCommon {
  source?: string | null
  recorded_at?: string | null
  cell_status?: CellStatus | null
  cell_status_reason?: CellStatusReason | null
  /** 接口返回的格子状态中文原因，与 cell_status_reason 同名加 _zh。旧响应没有这个字段。 */
  cell_status_reason_zh?: string | null
  /** 接口返回的缺失原因码。页面不另做翻译，只在没有中文时才退回现有对照表。 */
  missing_reason?: string | null
  /** 接口返回的缺失原因中文，与 missing_reason 同名加 _zh。旧响应没有这个字段。 */
  missing_reason_zh?: string | null
  move?: Move | null
  alert?: AlertLevel
  /** Sample size behind the alert, when the backend provides it. */
  alert_sample_n?: number | null
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

/** 赛果格上接口给出的原因码和同名中文。旧响应可以只有原因码，也可以两项都没有。 */
export interface ResultReason {
  null_reason?: string | null
  null_reason_zh?: string | null
  cell_status_reason?: string | null
  cell_status_reason_zh?: string | null
  missing_reason?: string | null
  missing_reason_zh?: string | null
}

/** 亚盘、大小球相对参考侧的五态。欧盘赛果不使用这套五态，字段留空。 */
export type MarketSettleCode = 'win' | 'win_half' | 'push' | 'lose_half' | 'lose'

/** 机构赛果单格共用字段。输赢由接口算好，页面只读这些字段。 */
interface BookMarketCell extends ResultReason {
  cell_status?: CellStatus | null
  /** 接口写好的短文案，例如「主+0.25」「2.25大」「胜 1.11」「走水」。 */
  display_text?: string | null
  settle_code?: MarketSettleCode | null
}

export interface ByBookResult {
  ah?: ({ line: number | null; side: 'home' | 'away' | null; push: boolean } & BookMarketCell) | null
  ou?: ({ line: number | null; side: 'over' | 'under' | null; push: boolean } & BookMarketCell) | null
  x1x2?: ({ side: 'home' | 'draw' | 'away' | null; odds: number | null } & BookMarketCell) | null
}

/** 基本面单项外壳的状态。与盘口格子的 cell_status 不是同一套。 */
export type FundStatus = 'ok' | 'not_filled' | 'unavailable' | 'not_defined'

/**
 * 基本面数值外壳。value 保留接口原数，页面展示时再格式化。
 * quality 取 measured、estimated、derived 等；estimated、derived 为等价布尔标记。
 */
export interface FundNumberCell {
  value: number | null
  status: FundStatus
  source: string | null
  as_of: string | null
  reason: string | null
  reason_zh: string | null
  quality: string | null
  estimated: boolean | null
  derived: boolean | null
}

/** 基本面 xG 六键，顺序即表上的列顺序。下半场只读 xg_2h_*，不由全场减上半场得到。 */
export const XG_KEYS = ['xg_ft_home', 'xg_ft_away', 'xg_ht_home', 'xg_ht_away', 'xg_2h_home', 'xg_2h_away'] as const
export type XgKey = (typeof XG_KEYS)[number]
export type Fundamentals = Record<XgKey, FundNumberCell>

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
    /** Canonical league name from the backend, present for only some leagues. */
    league_canonical?: string | null
    /** 后端还不能确认这场赛事时为 true。旧接口没有这个字段。 */
    league_pending_confirm?: boolean | null
    /** Canonical team names from the backend, present for only some matches. */
    home_team_canonical?: string | null
    away_team_canonical?: string | null
    /** True when the backend cannot identify the team yet; absent on backends without the field. */
    home_team_pending_confirm?: boolean | null
    away_team_pending_confirm?: boolean | null
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
    /** Result status from the backend (nested format result.status); absent on backends that do not serve it yet. */
    status?: 'finished' | 'refund' | 'postponed' | 'cancelled' | 'abandoned' | null
    home_goals: number | null
    away_goals: number | null
    total_goals: number | null
    outcome_1x2: 'home' | 'draw' | 'away' | null
    by_book: Partial<Record<Book, ByBookResult>> | null
    jc: {
      had: ({ side: 'home' | 'draw' | 'away' | null; odds: number | null; cell_status?: CellStatus } & ResultReason) | null
      hhad: ({ goal_line: number | null; side: 'home' | 'draw' | 'away' | null; odds: number | null; cell_status?: CellStatus } & ResultReason) | null
    } | null
  } | null
  settlement_by_book: Partial<Record<Book, { ah: SettleItem | null; ou: SettleItem | null; x1x2: SettleItem | null; jc: SettleItem | null } | null>> | null
  /** 基本面。接口尚未返回 fundamentals 时六列均为 not_filled，页面显示「-」。 */
  fundamentals: Fundamentals
  /** 前端标记：这一行哪些部分来自模拟数据 */
  _mock?: boolean
}
