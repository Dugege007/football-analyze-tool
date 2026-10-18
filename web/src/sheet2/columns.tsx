import { isTeamPending, teamAliasTip } from '../names/teamName'
import { leagueAliasTip } from '../names/leagueShort'
import { shownLeague, shownTeam } from '../names/shownName'
import { isLeaguePending } from '../names/leaguePending'
import { NameHover } from '../components/DisplayName'
import type { ColDef, ColGroupDef, ICellRendererParams } from 'ag-grid-community'
import { Tag, Tooltip } from 'antd'
import type { ReactNode } from 'react'
import { PendingMark, TeamPendingMark } from '../components/TeamPendingMark'
import { formatPick } from '../api/pickFormat'
import { alignClass, w, type WidthKey } from './columnWidths'
import {
  SETTLE_TEXT,
  STATUS_TEXT,
  ahLineText,
  ahMove,
  bjTime,
  sourceText,
  bookMarketResultView,
  fmtNum,
  oddsCellReason,
  ouMove,
  reasonHoverText,
  resultCellReason,
  statusOf,
  x1x2Move,
} from './format'
import { BOOK_NAME, PHASES, PHASE_NAME, type Phased, type X1x2Cell, type AvgDetail, type Book, type CellStatus, type FundNumberCell, type Move, type MoveDir, type Phase, type ResultReason, type Row } from './types'
import { XG_COLUMNS, xgCellView } from './xg'

export type Market = 'ah' | 'ou' | 'x1x2'
type Col = ColDef<Row> | ColGroupDef<Row>
type P = ICellRendererParams<Row>

const SEC = 'sec-start'
const WEEK_CHARS = ['日', '一', '二', '三', '四', '五', '六']
const WEEK_CLASS: Record<string, string> = { 一: 'wd-1', 二: 'wd-2', 三: 'wd-3', 四: 'wd-4', 五: 'wd-5', 六: 'wd-6', 日: 'wd-7' }

/**
 * 竞彩日与编号两列的底色共用这一个来源：优先取竞彩编号首字（接口真实格式如「五002」），
 * 没有编号时按竞彩日的星期推算；两列始终取同一个结果，保证同一竞彩日底色一致。
 */
function weekdayOf(r: Row | undefined): string | null {
  if (!r) return null
  const pre = r.match.jc_id?.slice(0, 1)
  if (pre && pre in WEEK_CLASS) return pre
  const d = new Date(`${r.match.jingcai_date}T12:00:00+08:00`)
  return Number.isNaN(d.getTime()) ? null : WEEK_CHARS[d.getDay()]
}
const dayClass = (r: Row | undefined) => WEEK_CLASS[weekdayOf(r) ?? ''] ?? ''

function kickoffText(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '-'
  const bj = new Date(d.getTime() + 8 * 3600e3)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(bj.getUTCMonth() + 1)}-${p(bj.getUTCDate())} ${p(bj.getUTCHours())}:${p(bj.getUTCMinutes())}`
}


/**
 * Void result statuses. result.status from the backend decides first; the older match.status values cancelled and postponed
 * still apply when that field is absent, so nothing changes on backends without it.
 * For these matches the score and settlement are null, so the result columns show grey text instead of an empty result.
 */
const VOID_TEXT: Record<string, { text: string; tip?: string }> = {
  refund: { text: '退款', tip: '竞彩网标为无效场次，不参与结算。' },
  postponed: { text: '推迟' },
  cancelled: { text: '取消' },
  abandoned: { text: '腰斩' },
}
function voidOf(r?: Row): { text: string; tip?: string } | null {
  const rs = r?.result?.status
  if (rs && VOID_TEXT[rs]) return VOID_TEXT[rs]
  const ms = r?.match.status
  return ms === 'cancelled' || ms === 'postponed' ? VOID_TEXT[ms] : null
}
function VoidText({ r }: { r?: Row }) {
  const v = voidOf(r)
  if (!v) return null
  const el = <span className="t-gray">{v.text}</span>
  return v.tip ? <Tooltip title={v.tip}>{el}</Tooltip> : el
}
const finished = (r?: Row) => !voidOf(r) && r?.match.status === 'finished' && r.result?.home_goals != null

// ───────────── 基本数据列 ─────────────
export function baseCols(): Col[] {
  // 九列全部固定在左侧，保证「基本」分组不被固定区和滚动区拆成两段
  return [{ groupId: 'base', headerName: '基本', marryChildren: true, children: baseChildren().map((c) => withAlign(c as ColDef<Row>, `base.${(c as ColDef<Row>).colId}` as WidthKey)) }]
}
/** 给列加上配置里的对齐类（单元格和表头一起） */
function withAlign(c: ColDef<Row>, k: WidthKey, sub?: string): ColDef<Row> {
  const al = alignClass(k, sub)
  const hc = c.headerClass
  const cc = c.cellClass
  return {
    ...c,
    headerClass: [typeof hc === 'string' ? hc : Array.isArray(hc) ? hc.join(' ') : '', 'al-hdr', al].join(' '),
    cellClass:
      typeof cc === 'function'
        ? (p) => [String((cc as (x: typeof p) => unknown)(p) ?? ''), al].join(' ')
        : [typeof cc === 'string' ? cc : Array.isArray(cc) ? cc.join(' ') : '', al].join(' '),
  }
}

function baseChildren(): Col[] {
  return [
    {
      colId: 'jcDay', headerName: '竞彩日', width: w('base.jcDay'), pinned: 'left',
      valueGetter: (p) => p.data?.match.jingcai_date ?? null,
      cellClass: (p) => dayClass(p.data),
      sort: 'asc', sortIndex: 0,
    },
    { colId: 'jcNo', headerName: '编号', width: w('base.jcNo'), pinned: 'left', valueGetter: (p) => p.data?.match.jc_id ?? '-', cellClass: (p) => dayClass(p.data), sort: 'asc', sortIndex: 1 },
    { colId: 'kickoff', headerName: '开赛时间', width: w('base.kickoff'), pinned: 'left', valueGetter: (p) => kickoffText(p.data?.match.kickoff_at ?? null) },
    { colId: 'league', headerName: '赛事', width: w('base.league'), pinned: 'left', valueGetter: (p) => shownLeague(p.data?.match.league, p.data?.match.league_canonical) ?? '-',
      // 格子里是常用名。有别名时悬停一行一个；没有别名时仍是来源赛事名。认不出的赛事在简称旁加待确认星号。
      cellRenderer: (p: P) => {
        const source = p.data?.match.league
        const canonical = p.data?.match.league_canonical
        const shown = shownLeague(source, canonical)
        return (
          <span>
            <NameHover tip={leagueAliasTip(shown, source, [source, canonical])}>{p.value}</NameHover>
            {isLeaguePending(source, canonical, p.data?.match.league_pending_confirm) && <PendingMark />}
          </span>
        )
      } },
    { colId: 'home', headerName: '主队', width: w('base.home'), pinned: 'left', valueGetter: (p) => shownTeam(p.data?.match.home_team, p.data?.match.home_team_canonical, p.data?.match.league, p.data?.match.league_canonical) ?? '-',
      // 格子里是常用名。悬停列出别名，一行一个，不加前缀。
      cellRenderer: (p: P) => (
        <span>
          <NameHover tip={teamAliasTip(p.data?.match.home_team, p.data?.match.home_team_canonical, shownLeague(p.data?.match.league, p.data?.match.league_canonical))}>{p.value}</NameHover>
          {isTeamPending(p.data?.match.home_team, p.data?.match.home_team_canonical, p.data?.match.home_team_pending_confirm) && <TeamPendingMark />}
        </span>
      ) },
    { colId: 'away', headerName: '客队', width: w('base.away'), pinned: 'left', valueGetter: (p) => shownTeam(p.data?.match.away_team, p.data?.match.away_team_canonical, p.data?.match.league, p.data?.match.league_canonical) ?? '-',
      // 格子里是常用名。悬停列出别名，一行一个，不加前缀。
      cellRenderer: (p: P) => (
        <span>
          <NameHover tip={teamAliasTip(p.data?.match.away_team, p.data?.match.away_team_canonical, shownLeague(p.data?.match.league, p.data?.match.league_canonical))}>{p.value}</NameHover>
          {isTeamPending(p.data?.match.away_team, p.data?.match.away_team_canonical, p.data?.match.away_team_pending_confirm) && <TeamPendingMark />}
        </span>
      ) },
    {
      colId: 'score', headerName: '比分', width: w('base.score'), pinned: 'left',
      cellRenderer: (p: P) => {
        const r = p.data
        if (!r) return null
        const s = r.match.status
        const sc = r.result?.home_goals != null && r.result?.away_goals != null ? `${r.result.home_goals}-${r.result.away_goals}` : null
        if (voidOf(r)) return <VoidText r={r} />
        if (s === 'not_started') return <span className="t-gray">未开始</span>
        if (s === 'live') return <span className="t-yellow">{sc ?? '比赛中'}</span>
        if (s === 'finished') return sc ?? '-'
        return '-'
      },
    },
    { colId: 'goals', headerName: '总进球', width: w('base.goals'), pinned: 'left', valueGetter: (p) => (finished(p.data) ? (p.data!.result!.total_goals ?? '-') : '-'),
      cellRenderer: (p: P) => (voidOf(p.data) ? <VoidText r={p.data} /> : p.value) },
    {
      colId: 'outcome', headerName: '赛果', width: w('base.outcome'), pinned: 'left',
      cellRenderer: (p: P) => {
        const r = p.data
        if (!r) return null
        if (voidOf(r)) return <VoidText r={r} />
        if (!finished(r)) return '-'
        const gd = r.result!.home_goals! - r.result!.away_goals!
        return gd > 0 ? <span className="t-red">胜</span> : gd < 0 ? <span className="t-green">负</span> : '平'
      },
    },
  ]
}

// ───────────── 通用格子 ─────────────
function StatusText({ st, reason, reasonZh, extra }: { st: CellStatus; reason?: string | null; reasonZh?: string | null; extra?: string | null }) {
  const t = STATUS_TEXT[st as Exclude<CellStatus, 'ok'>] ?? '无法获取'
  // 原因和样本数等补充说明放在同一个悬停里，避免两层提示叠在一起。中文优先用接口返回的 reasonZh。
  const lines = [reasonHoverText(reason, reasonZh), extra ?? null].filter(Boolean) as string[]
  const span = <span className={extra ? 't-gray alert-sample-tip' : 't-gray'}>{t}</span>
  return lines.length ? <Tooltip title={lines.map((l, k) => <div key={k}>{l}</div>)}>{span}</Tooltip> : span
}

function Mv({ text, dir, colored }: { text: string; dir: MoveDir | undefined; colored: boolean }) {
  if (!colored || !dir || dir === 'flat') return <>{text}</>
  return <span className={dir === 'up' ? 'mv-up' : 'mv-down'}>{text}</span>
}

// 没有映射的取值原样显示在「平票处理：」后面
const TIE_TEXT: Record<string, string> = { median: '按中位数', macau: '取澳门', pinnacle: '取平博', crown: '取皇冠', bet365: '取 Bet365', smaller_line: '取较小盘口' }
const DEVIG_TEXT: Record<string, string> = { power: '幂次法' }
const MODEL_TEXT: Record<string, string> = { poisson: '泊松模型' }
const EXCLUDE_TEXT: Record<string, string> = { deviation_too_large: '偏离多数盘口过大', conversion_failed: '模型算不出换算结果' }
const GE_SOURCE_TEXT: Record<string, string> = { ou_median_same_phase: '同一阶段各家大小球盘口的中位数（不含威廉）', default: '没有大小球数据，取默认值' }

/** 多家平均格悬停：按约定第 7.2 节字段显示（第 3 版字段缺失时退回第 2 版字段）。预警等级与样本数写在同一气泡顶部。 */
function AvgTip({ d, market, phase, notes, children }: { d: AvgDetail | null | undefined; market: Market; phase?: Phase; notes?: readonly string[]; children: ReactNode }) {
  const noteLines = (notes ?? []).filter((line) => line)
  // 没有平均明细时，预警说明仍单独悬停，避免把等级和样本数丢掉。
  if (!d) {
    if (!noteLines.length) return <>{children}</>
    return <Tooltip title={noteLines.join('')}><span className="alert-sample-tip">{children}</span></Tooltip>
  }
  const name = (b: string) => BOOK_NAME[b as Book] ?? b
  const names = (xs: string[] | undefined) => (xs && xs.length ? xs.map(name).join('、') : '无')
  const lineText = (v: number | null | undefined) => (v == null ? '-' : market === 'ah' ? ahLineText(v) : fmtNum(v))
  const averaged = d.averaged_books ?? d.books_used
  const missing = d.missing_books ?? Object.entries(d.books_missing ?? {}).map(([book, reason]) => ({ book, reason, reason_zh: null, missing_reason_zh: null }))
  const tie = d.tie_break && d.tie_break !== 'none' ? (TIE_TEXT[d.tie_break] ?? d.tie_break) : null
  const srcs = d.sources ?? {}
  const srcBooks = Object.keys(srcs).sort((a, b) => BOOK_ORDER.indexOf(a) - BOOK_ORDER.indexOf(b))
  const tip = (
    <div className="avg-tip">
      {noteLines.map((line) => <div key={line}>{line}</div>)}
      {phase === 'open' && (Object.keys(srcs).length === 0 || Object.values(srcs).some((x) => !x?.recorded_at)) && <div>初盘开盘时间不详，各机构开盘时刻可能不同</div>}
      {market !== 'x1x2' && <div>多数盘口：{lineText(d.majority_line)}</div>}
      {tie && <div>平票处理：{tie}{d.median_line != null ? `（中位数 ${market === 'ah' && d.median_line > 0 ? '+' : ''}${Number(d.median_line.toFixed(3))}）` : ''}</div>}
      <div>参与平均：{names(averaged)}{d.averaged_count != null ? `（${d.averaged_count} 家）` : ''}</div>
      {market !== 'x1x2' && d.voting_books && <div>参与投票：{names(d.voting_books)}</div>}
      {(d.excluded_books ?? []).length > 0 && (
        <div>剔除：{d.excluded_books!.map((e) => `${name(e.book)}（${EXCLUDE_TEXT[e.reason] ?? e.reason}，相差 ${e.steps} 档${e.original_line != null ? `，原盘口 ${lineText(e.original_line)}` : ''}${e.original_water?.length ? `，原水位 ${e.original_water.map(fmtNum).join(' / ')}` : ''}）`).join('、')}</div>
      )}
      {missing.length > 0 && <div>没有数据：{missing.map((m) => `${name(m.book)}（${reasonHoverText(m.reason, m.reason_zh || m.missing_reason_zh) ?? m.reason}）`).join('、')}</div>}
      {market === 'ah' && d.goal_expectation != null && (
        <div>总进球期望：{fmtNum(d.goal_expectation)}{d.goal_expectation_source ? `（${GE_SOURCE_TEXT[d.goal_expectation_source] ?? d.goal_expectation_source}）` : ''}</div>
      )}
      {d.devig_method && <div>去水方法：{DEVIG_TEXT[d.devig_method] ?? d.devig_method}</div>}
      {d.conversion_model && <div>换算模型：{MODEL_TEXT[d.conversion_model] ?? d.conversion_model}</div>}
      {(d.conversions ?? []).length > 0 ? (
        <table className="avg-tip-table">
          <thead>
            <tr><th>机构</th>{market !== 'x1x2' && <th>原盘口</th>}<th>原{market === 'x1x2' ? '赔率' : '水位'}</th>{market !== 'x1x2' && <th>换算后水位</th>}</tr>
          </thead>
          <tbody>
            {d.conversions!.map((c) => (
              <tr key={c.book}>
                <td>{name(c.book)}</td>
                {market !== 'x1x2' && <td>{lineText(c.original_line)}{c.steps > 0 ? `（差 ${c.steps} 档）` : ''}</td>}
                <td>{c.original_water.map(fmtNum).join(' / ')}</td>
                {market !== 'x1x2' && <td>{c.converted_water.map(fmtNum).join(' / ')}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        (d.converted ?? []).map((c) => (
          <div key={c.book}>换算：{name(c.book)} {lineText(c.raw_line)} {c.raw.map(fmtNum).join(' / ')} → {c.converted.map(fmtNum).join(' / ')}</div>
        ))
      )}
      {srcBooks.length > 0 && (
        <table className="avg-tip-table">
          <thead>
            <tr><th>机构</th><th>来源</th><th>记录时间</th></tr>
          </thead>
          <tbody>
            {srcBooks.map((b) => (
              <tr key={b}><td>{name(b)}</td><td>{sourceText(srcs[b]?.source)}</td><td>{srcs[b]?.recorded_at ? bjTime(srcs[b].recorded_at) : '不详'}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
  return (
    <Tooltip title={tip} placement="right" styles={{ root: { maxWidth: 460 } }}>
      <span className="avg-hover">{children}</span>
    </Tooltip>
  )
}

const BOOK_ORDER = ['macau', 'crown', 'pinnacle', 'william', 'bet365']
const ALERT_CLASS = { light: 'al-light', medium: 'al-medium', high: 'al-high' } as const

type Sub = 'left' | 'line' | 'mid' | 'right'
interface TripleSpec {
  id: string
  title: string
  first: boolean
  phase: Phase
  /** 取这一阶段的格子与升降 */
  get: (r: Row) => { cell: Record<string, unknown> | null | undefined; move: Move | null }
  subs: { key: Sub; field: string; header: string; wkey: WidthKey; fmt?: (v: number | null) => string; alert?: boolean }[]
  avg?: Market | null
  prefix?: (r: Row) => string | null
}

/** Chinese names of the alert levels for the hover text. */
const ALERT_NAME: Record<string, string> = { high: '高', medium: '中', light: '轻' }

/** 样本数为正时返回「样本数：N。」；为 0 时不返回提示。 */
function sampleText(n: number): string | null {
  return n > 0 ? `样本数：${n}。` : null
}

/** 一个阶段一组三列（主水 / 盘口 / 客水 等）；格子没有数据时左列跨三列显示状态文字。 */
function tripleGroup(t: TripleSpec): ColGroupDef<Row> {
  const n = t.subs.length
  const children: ColDef<Row>[] = t.subs.map((sub, i) => ({
    colId: `${t.id}.${sub.key}`,
    headerName: sub.header,
    width: w(sub.wkey),
    headerClass: ['sub-hdr', alignClass(sub.wkey, sub.header), i === 0 && t.first ? SEC : ''].join(' '),
    colSpan: i === 0 ? (p) => (p.data && statusOf(t.get(p.data).cell as { cell_status?: CellStatus }) !== 'ok' ? n : 1) : undefined,
    cellClass: (p) => {
      const cls = ['num-cell', alignClass(sub.wkey, sub.header), i === 0 && t.first ? SEC : '']
      if (sub.key === 'line') cls.push('line-cell')
      const c = p.data ? (t.get(p.data).cell as { alert?: keyof typeof ALERT_CLASS | null; cell_status?: CellStatus } | null) : null
      if (sub.alert && t.phase !== 'open' && c?.alert && statusOf(c) === 'ok') cls.push(ALERT_CLASS[c.alert])
      if (i === 0 && statusOf(c) !== 'ok') cls.push('span-status')
      return cls.join(' ')
    },
    cellRenderer: (p: P) => {
      const r = p.data
      if (!r) return null
      const { cell, move } = t.get(r)
      const st = statusOf(cell as { cell_status?: CellStatus })
      if (!cell || st !== 'ok') {
        if (i !== 0) return null
        // 空格子只在样本数为正时附上样本数；为 0 或缺失时不再附加说明。
        const emptyN = t.subs.some((x) => x.alert) ? ((cell as { alert_sample_n?: number | null } | undefined)?.alert_sample_n ?? null) : null
        const oddsReason = oddsCellReason(cell)
        const stxTip = <StatusText st={st} reason={oddsReason.reason} reasonZh={oddsReason.reasonZh} extra={t.subs.some((x) => x.alert) && emptyN ? sampleText(emptyN) : null} />
        return t.avg && cell?.avg_detail ? <AvgTip d={cell.avg_detail as AvgDetail} market={t.avg} phase={t.phase}>{stxTip}</AvgTip> : stxTip
      }
      const v = cell[sub.field] as number | null
      const text = (sub.fmt ?? fmtNum)(v)
      const pre = sub.key === 'line' && t.prefix ? t.prefix(r) : null
      const body = <>{pre}<Mv text={text} dir={move?.[sub.key] as MoveDir | undefined} colored={t.phase !== 'open'} /></>
      // 盘口列在接口给出预警等级或正样本数时附上说明。多家平均并入来源气泡，单机构仍用独立悬停。
      const sampleN = sub.alert ? ((cell as { alert_sample_n?: number | null }).alert_sample_n ?? null) : null
      const alertLv = sub.alert ? ((cell as { alert?: string | null }).alert ?? null) : null
      const tipParts = [alertLv && t.phase !== 'open' ? `预警等级：${ALERT_NAME[alertLv] ?? alertLv}。` : null, sampleN == null ? null : sampleText(sampleN)].filter((x): x is string => Boolean(x))
      if (t.avg) return <AvgTip d={cell.avg_detail as AvgDetail | null | undefined} market={t.avg} phase={t.phase} notes={tipParts}>{body}</AvgTip>
      return tipParts.length ? <Tooltip title={tipParts.join('')}><span className="alert-sample-tip">{body}</span></Tooltip> : body
    },
  }))
  return { groupId: t.id, headerName: t.title, headerClass: t.first ? SEC : undefined, children }
}

const SUBS: Record<Market, TripleSpec['subs']> = {
  ah: [
    { key: 'left', field: 'home_water', header: '主水', wkey: 'ah.water' },
    { key: 'line', field: 'line', header: '盘口', wkey: 'ah.line', fmt: (v) => (v == null ? '-' : ahLineText(v)), alert: true },
    { key: 'right', field: 'away_water', header: '客水', wkey: 'ah.water' },
  ],
  ou: [
    { key: 'left', field: 'over', header: '大球', wkey: 'ou.water' },
    { key: 'line', field: 'line', header: '盘口', wkey: 'ou.line', alert: true },
    { key: 'right', field: 'under', header: '小球', wkey: 'ou.water' },
  ],
  x1x2: [
    { key: 'left', field: 'home', header: '胜', wkey: 'x1x2.odds' },
    { key: 'mid', field: 'draw', header: '平', wkey: 'x1x2.odds' },
    { key: 'right', field: 'away', header: '负', wkey: 'x1x2.odds' },
  ],
}
const MOVE_FN = { ah: ahMove, ou: ouMove, x1x2: x1x2Move } as const

function oddsGroup(book: Book, market: Market, phase: Phase, first: boolean): ColGroupDef<Row> {
  const label = market === 'ah' ? `亚${PHASE_NAME[phase]}` : market === 'ou' ? `大小${PHASE_NAME[phase]}` : `欧${PHASE_NAME[phase]}`
  return tripleGroup({
    id: `${book}.${market}.${phase}`,
    title: label,
    first,
    phase,
    avg: book === 'avg' ? market : null,
    subs: SUBS[market],
    get: (r) => {
      const cells = r[market]?.[book] as Phased<object> | undefined
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return { cell: cells?.[phase] as Record<string, unknown> | null | undefined, move: (MOVE_FN[market] as any)(cells, phase) }
    },
  })
}

/**
 * 亚盘赛果、大小赛果、欧盘赛果只读接口 result.by_book。
 * 未完赛、整段 by_book 为空、格子缺失或状态为 pending 时显示「-」。
 * 只有该格 cell_status 为 unavailable 或 insufficient 时显示「无法获取」。
 * 状态为 ok 时优先显示 display_text；没有短文案才按接口已给的 side、line、odds 格式化。
 */
function resultCol(book: Book, market: Market, first: boolean): ColDef<Row> {
  const label = market === 'ah' ? '亚盘赛果' : market === 'ou' ? '大小赛果' : '欧盘赛果'
  return {
    colId: `${book}.res.${market}`,
    headerName: label,
    width: w(`res.${market}`),
    headerClass: ['al-hdr', alignClass(`res.${market}`), first ? SEC : ''].join(' '),
    cellClass: [alignClass(`res.${market}`), first ? SEC : ''].join(' '),
    cellRenderer: (p: P) => {
      const r = p.data
      if (voidOf(r)) return <VoidText r={r} />
      if (!r || !finished(r)) return '-'
      const view = bookMarketResultView(r.result?.by_book, book, market)
      if (view.kind === 'dash') return '-'
      if (view.kind === 'status') return <StatusText st={view.status} reason={view.reason} reasonZh={view.reasonZh} />
      if (view.tone === 'orange') return <span className="t-orange">{view.text}</span>
      if (view.tone === 'blue') return <span className="t-blue">{view.text}</span>
      return view.text
    },
  }
}

/** 赛果为空时显示「无法获取」或「未填写」，悬停读取该格接口返回的中文原因。 */
function ResultStatus({ cell }: { cell?: ({ cell_status?: CellStatus | null } & ResultReason) | null }) {
  const reason = resultCellReason(cell)
  return <StatusText st={cell?.cell_status ?? 'unavailable'} reason={reason.reason} reasonZh={reason.reasonZh} />
}

function wdlSpan(side: 'home' | 'draw' | 'away', odds: number | null, prefix = ''): ReactNode {
  const t = `${prefix}${side === 'home' ? '胜' : side === 'draw' ? '平' : '负'} ${fmtNum(odds)}`
  return side === 'home' ? <span className="t-orange">{t}</span> : side === 'away' ? <span className="t-blue">{t}</span> : t
}

const MARKET_ORDER: Market[] = ['ah', 'ou', 'x1x2']

/** 通用机构数据列组件：澳门、皇冠、平博、威廉、Bet365、多家平均共用。 */
export function bookCols(book: Book, markets: Market[] = MARKET_ORDER, resultOnly = false): ColGroupDef<Row> {
  const children: Col[] = []
  markets.forEach((m, i) => children.push(resultCol(book, m, i === 0)))
  if (!resultOnly) for (const m of markets) PHASES.forEach((ph, i) => children.push(oddsGroup(book, m, ph, i === 0)))
  return { groupId: `book.${book}`, headerName: BOOK_NAME[book], headerClass: SEC, children }
}

// ───────────── 竞彩数据列 ─────────────
function signedJc(n: number | null): string {
  if (n == null) return ''
  return n > 0 ? `+${n}` : n < 0 ? `${n}` : '0'
}

export function jcCols(resultOnly = false, onlyHad = false): ColGroupDef<Row> {
  const children: Col[] = [
    {
      colId: 'jc.res.had', headerName: '胜平负赛果', width: w('jc.res.had'), headerClass: `al-hdr ${alignClass('jc.res.had')} ${SEC}`, cellClass: `${alignClass('jc.res.had')} ${SEC}`,
      cellRenderer: (p: P) => {
        const r = p.data
        if (voidOf(r)) return <VoidText r={r} />
        if (!r || !finished(r)) return '-'
        const h = r.result?.jc?.had
        if (!h?.side) return <ResultStatus cell={h} />
        return wdlSpan(h.side, h.odds)
      },
    },
  ]
  if (!onlyHad)
    children.push({
      colId: 'jc.res.hhad', headerName: '让球胜平负赛果', width: w('jc.res.hhad'), headerClass: `al-hdr ${alignClass('jc.res.hhad')}`, cellClass: alignClass('jc.res.hhad'),
      cellRenderer: (p: P) => {
        const r = p.data
        if (voidOf(r)) return <VoidText r={r} />
        if (!r || !finished(r)) return '-'
        const h = r.result?.jc?.hhad
        if (!h?.side) return <ResultStatus cell={h} />
        return wdlSpan(h.side, h.odds, signedJc(h.goal_line))
      },
    })
  if (!resultOnly) {
    for (const kind of ['had', 'hhad'] as const) {
      PHASES.forEach((ph, i) => {
        const subs: TripleSpec['subs'] = SUBS.x1x2.map((x) => ({ ...x, wkey: kind === 'had' ? 'jc.had.odds' : 'jc.hhad.odds' }))
        children.push(
          tripleGroup({
            id: `jc.${kind}.${ph}`,
            title: `${kind === 'had' ? '胜平负' : '让球胜平负'}${PHASE_NAME[ph]}`,
            first: i === 0,
            phase: ph,
            subs,
            prefix: undefined,
            get: (r) => {
              const cells = (kind === 'had' ? r.jc_1x2 : r.jc_hhad) as Phased<X1x2Cell>
              return { cell: cells?.[ph] as unknown as Record<string, unknown> | null, move: x1x2Move(cells, ph) }
            },
          }),
        )
        if (kind === 'hhad') {
          // 让球数单独一列，放在该阶段三列之前
          const g = children[children.length - 1] as ColGroupDef<Row>
          g.children.unshift({
            colId: `jc.hhad.${ph}.goal`,
            headerName: '让球',
            width: w('jc.hhad.goal'),
            headerClass: ['sub-hdr', alignClass('jc.hhad.goal'), i === 0 ? SEC : ''].join(' '),
            // 让球数按盘口规则加粗
            cellClass: ['num-cell', 'line-cell', alignClass('jc.hhad.goal'), i === 0 ? SEC : ''].join(' '),
            valueGetter: (p) => {
              const c = p.data?.jc_hhad?.[ph]
              return c && statusOf(c) === 'ok' ? `(${signedJc(c.goal_line)})` : ''
            },
          })
          ;(g.children[1] as ColDef<Row>).headerClass = `sub-hdr ${alignClass('jc.hhad.odds', '胜')}`
          ;(g.children[1] as ColDef<Row>).cellClass = (p) => {
            const c = p.data?.jc_hhad?.[ph]
            return ['num-cell', alignClass('jc.hhad.odds', '胜'), c && statusOf(c) !== 'ok' ? 'span-status' : ''].join(' ')
          }
        }
      })
    }
  }
  return { groupId: 'book.jc', headerName: '竞彩', headerClass: SEC, children }
}

// ───────────── 基本面列 ─────────────
function XgText({ cell }: { cell: FundNumberCell | null | undefined }) {
  const view = xgCellView(cell)
  if (view.kind === 'dash') return view.text
  if (view.kind === 'unavailable') return <StatusText st="unavailable" reason={view.reason} reasonZh={view.reasonZh} />
  if (!view.mark) return view.text
  const el = <span className={view.className}>{view.text}</span>
  return view.tip ? <Tooltip title={view.tip}>{el}</Tooltip> : el
}

/**
 * 基本面数据组。目前只亮 xG 六列，放在组末（近况、交锋、排名、人气、伤停、天气尚未亮列）。
 * 只挂在「基本面」标签，预测标签和机构标签不使用本组。
 */
export function fundCols(): ColGroupDef<Row> {
  const children = XG_COLUMNS.map((col, i) =>
    withAlign(
      {
        colId: `fund.${col.key}`,
        headerName: col.header,
        width: w('fund.xg'),
        headerClass: i === 0 ? SEC : undefined,
        cellClass: i === 0 ? SEC : undefined,
        cellRenderer: (p: P) => <XgText cell={p.data?.fundamentals?.[col.key]} />,
      },
      'fund.xg',
    ),
  )
  return { groupId: 'fund', headerName: '基本面', headerClass: SEC, children }
}

// ───────────── 预测列 ─────────────
export type PredBlock = 'ah' | 'ou' | 'x1x2' | 'jc'
const SETTLE_CLASS = { win: 'f-win', win_half: 'f-winhalf', push: '', lose_half: 'f-losehalf', lose: 'f-lose', no_bet: '' } as const

export function predCols(book: Book, blocks: PredBlock[]): ColGroupDef<Row> {
  const children: Col[] = []
  for (const b of blocks) {
    const name = b === 'ah' ? '亚盘预测' : b === 'ou' ? '大小预测' : b === 'x1x2' ? '欧盘预测' : '竞彩预测'
    children.push({
      colId: `pred.${b}`, headerName: name, width: w(`pred.${b}`), headerClass: `al-hdr ${alignClass(`pred.${b}`)} ${SEC}`, cellClass: `${alignClass(`pred.${b}`)} ${SEC}`,
      cellRenderer: (p: P) => {
        const r = p.data
        if (!r || b !== 'ah') return '-'
        const dir = r.prediction?.direction
        if (dir !== '主' && dir !== '客') return '-'
        const s = r.settlement_by_book?.[book]?.ah
        const line = s?.line ?? r.prediction?.line ?? null
        return <span className={dir === '主' ? 't-orange' : 't-blue'}>{formatPick({ market: 'ah', side: dir, line })}</span>
      },
    })
    children.push({
      colId: `pred.${b}.res`, headerName: '结果', width: w(`pred.${b}.res`), headerClass: `al-hdr ${alignClass(`pred.${b}.res`)}`,
      cellClass: (p) => {
        if (b !== 'ah') return alignClass(`pred.${b}.res`)
        const code = p.data?.settlement_by_book?.[book]?.ah?.code
        return [alignClass(`pred.${b}.res`), finished(p.data) && code ? SETTLE_CLASS[code] : ''].join(' ')
      },
      cellRenderer: (p: P) => {
        const r = p.data
        if (!r || b !== 'ah') return '-'
        const dir = r.prediction?.direction
        if ((dir !== '主' && dir !== '客') || !finished(r)) return '-'
        if (!r.settlement_by_book) return <StatusText st="unavailable" />
        const s = r.settlement_by_book[book]?.ah
        if (!s) return <StatusText st="unavailable" />
        // 是否标出实际机构，只看该玩法结算项的 fallback_book。机构名取 settled_with，页面不自行判断该用哪一家。
        return (
          <>
            {SETTLE_TEXT[s.code]}
            {s.fallback_book && (
              <Tooltip title={`实际按${BOOK_NAME[s.settled_with as Book] ?? s.settled_with}临盘结算`}>
                <Tag className="fallback-tag" bordered={false}>
                  {BOOK_NAME[s.settled_with as Book] ?? s.settled_with}
                </Tag>
              </Tooltip>
            )}
          </>
        )
      },
    })
  }
  return { groupId: 'pred', headerName: '预测', headerClass: SEC, children }
}
