import type { ColDef, ColGroupDef, ICellRendererParams } from 'ag-grid-community'
import { Tag, Tooltip } from 'antd'
import type { ReactNode } from 'react'
import { formatPick } from '../api/pickFormat'
import {
  REASON_TEXT,
  SETTLE_TEXT,
  STATUS_TEXT,
  ahLineText,
  ahMove,
  fmtNum,
  ouMove,
  statusOf,
  x1x2Move,
} from './format'
import { BOOK_NAME, PHASES, PHASE_NAME, type Phased, type X1x2Cell, type AvgDetail, type Book, type CellStatus, type Move, type MoveDir, type Phase, type Row } from './types'

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

const finished = (r?: Row) => r?.match.status === 'finished' && r.result?.home_goals != null

// ───────────── 基本数据列 ─────────────
export function baseCols(): Col[] {
  // 九列全部固定在左侧，保证「基本」分组不被固定区和滚动区拆成两段
  return [{ groupId: 'base', headerName: '基本', marryChildren: true, children: baseChildren() }]
}
function baseChildren(): Col[] {
  return [
    {
      colId: 'jcDay', headerName: '竞彩日', width: 104, pinned: 'left',
      valueGetter: (p) => p.data?.match.jingcai_date ?? null,
      cellClass: (p) => dayClass(p.data),
      sort: 'asc', sortIndex: 0,
    },
    { colId: 'jcNo', headerName: '编号', width: 88, pinned: 'left', valueGetter: (p) => p.data?.match.jc_id ?? '-', cellClass: (p) => dayClass(p.data), sort: 'asc', sortIndex: 1 },
    { colId: 'kickoff', headerName: '开赛时间', width: 96, pinned: 'left', valueGetter: (p) => kickoffText(p.data?.match.kickoff_at ?? null) },
    { colId: 'league', headerName: '赛事', width: 96, pinned: 'left', valueGetter: (p) => p.data?.match.league ?? '-' },
    { colId: 'home', headerName: '主队', width: 88, pinned: 'left', valueGetter: (p) => p.data?.match.home_team ?? '-' },
    { colId: 'away', headerName: '客队', width: 88, pinned: 'left', valueGetter: (p) => p.data?.match.away_team ?? '-' },
    {
      colId: 'score', headerName: '比分', width: 58, pinned: 'left',
      cellRenderer: (p: P) => {
        const r = p.data
        if (!r) return null
        const s = r.match.status
        const sc = r.result?.home_goals != null && r.result?.away_goals != null ? `${r.result.home_goals}-${r.result.away_goals}` : null
        if (s === 'not_started') return <span className="t-gray">未开始</span>
        if (s === 'live') return <span className="t-yellow">{sc ?? '比赛中'}</span>
        if (s === 'finished') return sc ?? '-'
        return '-'
      },
    },
    { colId: 'goals', headerName: '总进球', width: 66, pinned: 'left', valueGetter: (p) => (finished(p.data) ? (p.data!.result!.total_goals ?? '-') : '-') },
    {
      colId: 'outcome', headerName: '赛果', width: 56, pinned: 'left',
      cellRenderer: (p: P) => {
        const r = p.data
        if (!r) return null
        if (r.match.status === 'cancelled') return <span className="t-gray">取消</span>
        if (r.match.status === 'postponed') return <span className="t-gray">推迟</span>
        if (!finished(r)) return '-'
        const gd = r.result!.home_goals! - r.result!.away_goals!
        return gd > 0 ? <span className="t-red">胜</span> : gd < 0 ? <span className="t-green">负</span> : '平'
      },
    },
  ]
}

// ───────────── 通用格子 ─────────────
function StatusText({ st, reason }: { st: CellStatus; reason?: string | null }) {
  const t = STATUS_TEXT[st as Exclude<CellStatus, 'ok'>] ?? '无法获取'
  const span = <span className="t-gray">{t}</span>
  return reason ? <Tooltip title={REASON_TEXT[reason] ?? reason}>{span}</Tooltip> : span
}

function Mv({ text, dir, colored }: { text: string; dir: MoveDir | undefined; colored: boolean }) {
  if (!colored || !dir || dir === 'flat') return <>{text}</>
  return <span className={dir === 'up' ? 'mv-up' : 'mv-down'}>{text}{dir === 'up' ? '↑' : '↓'}</span>
}

function AvgTip({ d, market, children }: { d: AvgDetail | null | undefined; market: Market; children: ReactNode }) {
  if (!d) return <>{children}</>
  const name = (b: string) => BOOK_NAME[b as Book] ?? b
  const tip = (
    <div className="avg-tip">
      <div>参与：{d.books_used.length ? d.books_used.map(name).join('、') : '无'}</div>
      {Object.keys(d.books_missing).length > 0 && (
        <div>
          缺席：{Object.entries(d.books_missing).map(([b, r]) => `${name(b)}（${REASON_TEXT[r] ?? r}）`).join('、')}
        </div>
      )}
      {d.majority_line != null && <div>多数盘口：{market === 'ah' ? ahLineText(d.majority_line) : fmtNum(d.majority_line)}</div>}
      {(d.converted ?? []).map((c) => (
        <div key={c.book}>
          换算：{name(c.book)} {market === 'ah' ? ahLineText(c.raw_line) : fmtNum(c.raw_line)} {c.raw.map(fmtNum).join(' / ')} → {c.converted.map(fmtNum).join(' / ')}
        </div>
      ))}
    </div>
  )
  return (
    <Tooltip title={tip} placement="right">
      <span className="avg-hover">{children}</span>
    </Tooltip>
  )
}

const ALERT_CLASS = { light: 'al-light', medium: 'al-medium', high: 'al-high' } as const

type Sub = 'left' | 'line' | 'mid' | 'right'
interface TripleSpec {
  id: string
  title: string
  first: boolean
  phase: Phase
  /** 取这一阶段的格子与升降 */
  get: (r: Row) => { cell: Record<string, unknown> | null | undefined; move: Move | null }
  subs: { key: Sub; field: string; header: string; fmt?: (v: number | null) => string; alert?: boolean }[]
  avg?: Market | null
  prefix?: (r: Row) => string | null
}

/** 一个阶段一组三列（主水 / 盘口 / 客水 等）；格子没有数据时左列跨三列显示状态文字。 */
function tripleGroup(t: TripleSpec): ColGroupDef<Row> {
  const n = t.subs.length
  const children: ColDef<Row>[] = t.subs.map((sub, i) => ({
    colId: `${t.id}.${sub.key}`,
    headerName: sub.header,
    width: sub.key === 'line' ? 58 : 52,
    headerClass: ['sub-hdr', i === 0 && t.first ? SEC : ''].join(' '),
    colSpan: i === 0 ? (p) => (p.data && statusOf(t.get(p.data).cell as { cell_status?: CellStatus }) !== 'ok' ? n : 1) : undefined,
    cellClass: (p) => {
      const cls = ['num-cell', i === 0 && t.first ? SEC : '']
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
      if (!cell || st !== 'ok') return i === 0 ? <StatusText st={st} reason={(cell?.cell_status_reason as string) ?? null} /> : null
      const v = cell[sub.field] as number | null
      const text = (sub.fmt ?? fmtNum)(v)
      const pre = sub.key === 'line' && t.prefix ? t.prefix(r) : null
      const body = <>{pre}<Mv text={text} dir={move?.[sub.key] as MoveDir | undefined} colored={t.phase !== 'open'} /></>
      return t.avg ? <AvgTip d={cell.avg_detail as AvgDetail | null | undefined} market={t.avg}>{body}</AvgTip> : body
    },
  }))
  return { groupId: t.id, headerName: t.title, headerClass: t.first ? SEC : undefined, children }
}

const SUBS: Record<Market, TripleSpec['subs']> = {
  ah: [
    { key: 'left', field: 'home_water', header: '主水' },
    { key: 'line', field: 'line', header: '盘口', fmt: (v) => (v == null ? '-' : ahLineText(v)), alert: true },
    { key: 'right', field: 'away_water', header: '客水' },
  ],
  ou: [
    { key: 'left', field: 'over', header: '大球' },
    { key: 'line', field: 'line', header: '盘口', alert: true },
    { key: 'right', field: 'under', header: '小球' },
  ],
  x1x2: [
    { key: 'left', field: 'home', header: '胜' },
    { key: 'mid', field: 'draw', header: '平' },
    { key: 'right', field: 'away', header: '负' },
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

function resultCol(book: Book, market: Market, first: boolean): ColDef<Row> {
  const label = market === 'ah' ? '亚盘赛果' : market === 'ou' ? '大小赛果' : '欧盘赛果'
  return {
    colId: `${book}.res.${market}`,
    headerName: label,
    width: 92,
    headerClass: first ? SEC : undefined,
    cellClass: first ? SEC : undefined,
    cellRenderer: (p: P) => {
      const r = p.data
      if (!r || !finished(r)) return '-'
      const bb = r.result?.by_book
      if (!bb) return <StatusText st="unavailable" />
      const x = bb[book]?.[market]
      if (!x) return <StatusText st="unavailable" />
      if (market === 'ah') {
        const a = x as { line: number | null; side: 'home' | 'away' | null; push: boolean; cell_status?: CellStatus }
        if (a.push) return '走水'
        if (!a.side) return <StatusText st={a.cell_status ?? 'unavailable'} />
        const side = a.side === 'home' ? '主' : '客'
        return <span className={a.side === 'home' ? 't-orange' : 't-blue'}>{formatPick({ market: 'ah', side, line: a.line })}</span>
      }
      if (market === 'ou') {
        const o = x as { line: number | null; side: 'over' | 'under' | null; push: boolean; cell_status?: CellStatus }
        if (o.push) return '走水'
        if (!o.side) return <StatusText st={o.cell_status ?? 'unavailable'} />
        return <span className={o.side === 'over' ? 't-orange' : 't-blue'}>{formatPick({ market: 'ou', side: o.side === 'over' ? '大' : '小', line: o.line })}</span>
      }
      const e = x as { side: 'home' | 'draw' | 'away' | null; odds: number | null; cell_status?: CellStatus }
      if (!e.side) return <StatusText st={e.cell_status ?? 'unavailable'} />
      return wdlSpan(e.side, e.odds)
    },
  }
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
      colId: 'jc.res.had', headerName: '胜平负赛果', width: 100, headerClass: SEC, cellClass: SEC,
      cellRenderer: (p: P) => {
        const r = p.data
        if (!r || !finished(r)) return '-'
        const h = r.result?.jc?.had
        if (!h?.side) return <StatusText st={h?.cell_status ?? 'unavailable'} />
        return wdlSpan(h.side, h.odds)
      },
    },
  ]
  if (!onlyHad)
    children.push({
      colId: 'jc.res.hhad', headerName: '让球胜平负赛果', width: 118,
      cellRenderer: (p: P) => {
        const r = p.data
        if (!r || !finished(r)) return '-'
        const h = r.result?.jc?.hhad
        if (!h?.side) return <StatusText st={h?.cell_status ?? 'unavailable'} />
        return wdlSpan(h.side, h.odds, signedJc(h.goal_line))
      },
    })
  if (!resultOnly) {
    for (const kind of ['had', 'hhad'] as const) {
      PHASES.forEach((ph, i) => {
        const subs: TripleSpec['subs'] = SUBS.x1x2.map((x) => ({ ...x }))
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
            width: 50,
            headerClass: ['sub-hdr', i === 0 ? SEC : ''].join(' '),
            cellClass: ['num-cell', i === 0 ? SEC : ''].join(' '),
            valueGetter: (p) => {
              const c = p.data?.jc_hhad?.[ph]
              return c && statusOf(c) === 'ok' ? `(${signedJc(c.goal_line)})` : ''
            },
          })
          ;(g.children[1] as ColDef<Row>).headerClass = 'sub-hdr'
          ;(g.children[1] as ColDef<Row>).cellClass = (p) => {
            const c = p.data?.jc_hhad?.[ph]
            return ['num-cell', c && statusOf(c) !== 'ok' ? 'span-status' : ''].join(' ')
          }
        }
      })
    }
  }
  return { groupId: 'book.jc', headerName: '竞彩', headerClass: SEC, children }
}

// ───────────── 预测列 ─────────────
export type PredBlock = 'ah' | 'ou' | 'x1x2' | 'jc'
const SETTLE_CLASS = { win: 'f-win', win_half: 'f-winhalf', push: '', lose_half: 'f-losehalf', lose: 'f-lose', no_bet: '' } as const

export function predCols(book: Book, blocks: PredBlock[]): ColGroupDef<Row> {
  const children: Col[] = []
  for (const b of blocks) {
    const name = b === 'ah' ? '亚盘预测' : b === 'ou' ? '大小预测' : b === 'x1x2' ? '欧盘预测' : '竞彩预测'
    children.push({
      colId: `pred.${b}`, headerName: name, width: b === 'ah' ? 96 : 86, headerClass: SEC, cellClass: SEC,
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
      colId: `pred.${b}.res`, headerName: '结果', width: b === 'ah' ? 120 : 64,
      cellClass: (p) => {
        if (b !== 'ah') return ''
        const code = p.data?.settlement_by_book?.[book]?.ah?.code
        return finished(p.data) && code ? SETTLE_CLASS[code] : ''
      },
      cellRenderer: (p: P) => {
        const r = p.data
        if (!r || b !== 'ah') return '-'
        const dir = r.prediction?.direction
        if ((dir !== '主' && dir !== '客') || !finished(r)) return '-'
        if (!r.settlement_by_book) return <StatusText st="unavailable" />
        const s = r.settlement_by_book[book]?.ah
        if (!s) return <StatusText st="unavailable" />
        // 是否标「按澳门结算」只看该玩法结算项的 fallback_book：亚盘、大小在多家平均下退回澳门时标注；
        // 欧盘多家平均有平均值时按多家平均结算，后端返回 fallback_book=false，不标注。
        return (
          <>
            {SETTLE_TEXT[s.code]}
            {s.fallback_book && (
              <Tag className="fallback-tag" bordered={false}>
                按澳门结算
              </Tag>
            )}
          </>
        )
      },
    })
  }
  return { groupId: 'pred', headerName: '预测', headerClass: SEC, children }
}
