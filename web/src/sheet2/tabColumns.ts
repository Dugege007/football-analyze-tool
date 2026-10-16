/**
 * 数据表标签顺序与各标签的列装配。
 * 基本面组（含 xG 六列）只挂在「基本面」标签。预测标签只有基本列和预测列。机构标签不加基本面组。
 */
import type { ColDef, ColGroupDef } from 'ag-grid-community'
import { baseCols, bookCols, fundCols, jcCols, predCols, type Market } from './columns'
import type { Book, Row } from './types'

export type SheetTabId =
  | 'predict'
  | 'fundamentals'
  | 'cmp_ah'
  | 'cmp_ou'
  | 'cmp_x1x2'
  | 'avg'
  | 'macau'
  | 'crown'
  | 'pinnacle'
  | 'william'
  | 'bet365'
  | 'jc'

export const OVERVIEW_TABS: { value: SheetTabId; label: string }[] = [
  { value: 'predict', label: '预测' },
  { value: 'fundamentals', label: '基本面' },
  { value: 'cmp_ah', label: '亚盘对照' },
  { value: 'cmp_ou', label: '大小对照' },
  { value: 'cmp_x1x2', label: '欧赔对照' },
]

export const BOOK_TABS: { value: SheetTabId; label: string }[] = [
  { value: 'avg', label: '多家平均' },
  { value: 'macau', label: '澳门' },
  { value: 'crown', label: '皇冠' },
  { value: 'pinnacle', label: '平博' },
  { value: 'william', label: '威廉' },
  { value: 'bet365', label: 'Bet365' },
  { value: 'jc', label: '竞彩' },
]

export const ALL_SHEET_TABS: SheetTabId[] = [...OVERVIEW_TABS, ...BOOK_TABS].map((t) => t.value)

export const CMP_MARKET: Partial<Record<SheetTabId, Market>> = { cmp_ah: 'ah', cmp_ou: 'ou', cmp_x1x2: 'x1x2' }

const CMP_BOOKS: Book[] = ['avg', 'macau', 'crown', 'pinnacle', 'william', 'bet365']
const LOCKED_BOOKS: Book[] = ['avg', 'macau', 'crown', 'pinnacle', 'william', 'bet365']

export type SheetCol = ColDef<Row> | ColGroupDef<Row>

export function buildSheetColumns(opts: {
  tab: SheetTabId
  settleBook: Book
  resultOnly: boolean
  orderBooks: (def: Book[]) => Book[]
}): SheetCol[] {
  const { tab, settleBook, resultOnly, orderBooks } = opts
  const cols: SheetCol[] = [...baseCols()]
  const cmp = CMP_MARKET[tab]
  if (tab === 'predict') {
    cols.push(predCols(settleBook, ['ah', 'ou', 'x1x2', 'jc']))
  } else if (tab === 'fundamentals') {
    cols.push(fundCols(), predCols(settleBook, ['ah', 'ou', 'x1x2', 'jc']))
  } else if (tab === 'jc') {
    cols.push(predCols(settleBook, ['jc']), jcCols())
  } else if (cmp) {
    cols.push(predCols(settleBook, [cmp]))
    for (const b of orderBooks(CMP_BOOKS)) cols.push(bookCols(b, [cmp], resultOnly))
    if (cmp === 'x1x2') cols.push(jcCols(true, true))
  } else if (LOCKED_BOOKS.includes(tab as Book)) {
    cols.push(predCols(settleBook, ['ah', 'ou', 'x1x2', 'jc']), bookCols(tab as Book))
  }
  return cols
}

export function columnIds(cols: SheetCol[]): string[] {
  const ids: string[] = []
  for (const col of cols) {
    if ('children' in col && col.children) ids.push(...columnIds(col.children as SheetCol[]))
    else if ('colId' in col && col.colId) ids.push(String(col.colId))
  }
  return ids
}
