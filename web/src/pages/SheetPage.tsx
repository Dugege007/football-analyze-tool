/**
 * 数据表页（/sheet）第 1 期：按用户 2026-10-14 修订的《数据表页面意见》重做。
 * 两组共 11 个标签；字段名照后端《数据表接口字段约定（第 1 期）》；接口没提供的字段显示「无法获取」。
 * 页面不写解释性文字，说明放在小问号里（web/UI-GUIDELINES.md）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Checkbox, DatePicker, Segmented, Select, Space, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import {
  CellStyleModule,
  ClientSideRowModelModule,
  ColumnApiModule,
  LocaleModule,
  ModuleRegistry,
  RowStyleModule,
  TooltipModule,
  ValidationModule,
  themeQuartz,
} from 'ag-grid-community'
import { AgGridReact } from 'ag-grid-react'
import { AG_GRID_LOCALE_CN } from '@ag-grid-community/locale'
import { HelpTip } from '../components/HelpTip'
import { DEFAULT_STRATEGY } from '../api/strategies'
import { useDbSource } from '../api/dataSource'
import { DataSourceSwitch } from '../api/DataSourceBar'
import { baseCols, bookCols, jcCols, predCols, type Market } from '../sheet2/columns'
import { apiBooks, loadRows, type DataMode } from '../sheet2/load'
import { BOOKS, BOOK_NAME, type Book, type Row } from '../sheet2/types'
import '../sheet2/sheet2.css'

ModuleRegistry.registerModules([
  ClientSideRowModelModule,
  CellStyleModule,
  RowStyleModule,
  ColumnApiModule,
  TooltipModule,
  LocaleModule,
  ...(import.meta.env.DEV ? [ValidationModule] : []),
])

const { RangePicker } = DatePicker

type TabId = 'predict' | 'avg' | 'cmp_ah' | 'cmp_ou' | 'cmp_x1x2' | 'macau' | 'crown' | 'pinnacle' | 'william' | 'bet365' | 'jc'
const OVERVIEW: { value: TabId; label: string }[] = [
  { value: 'predict', label: '预测' },
  { value: 'avg', label: '多家平均' },
  { value: 'cmp_ah', label: '亚盘对照' },
  { value: 'cmp_ou', label: '大小对照' },
  { value: 'cmp_x1x2', label: '欧赔对照' },
]
const BOOK_TABS: { value: TabId; label: string }[] = [
  { value: 'macau', label: '澳门' },
  { value: 'crown', label: '皇冠' },
  { value: 'pinnacle', label: '平博' },
  { value: 'william', label: '威廉' },
  { value: 'bet365', label: 'Bet365' },
  { value: 'jc', label: '竞彩' },
]
const ALL_TABS = [...OVERVIEW, ...BOOK_TABS].map((t) => t.value)
const CMP_MARKET: Partial<Record<TabId, Market>> = { cmp_ah: 'ah', cmp_ou: 'ou', cmp_x1x2: 'x1x2' }
const CMP_BOOKS: Book[] = ['avg', 'macau', 'crown', 'pinnacle', 'william', 'bet365']

/** 按接口顶层 books 排机构列；接口没给时用默认顺序。多家平均固定在最前（第 1 期已定），其余按接口顺序。 */
function orderBooks(def: Book[]): Book[] {
  const ab = apiBooks?.filter((b): b is Book => (def as string[]).includes(b)) ?? []
  if (!ab.length) return def
  const rest = ab.filter((b) => b !== 'avg')
  return [...(def.includes('avg') ? (['avg'] as Book[]) : []), ...rest, ...def.filter((b) => b !== 'avg' && !rest.includes(b))]
}
/** 每个表头都带悬停全名，表头被省略时也能看到 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function withHeaderTips(cols: any[]): any[] {
  return cols.map((c) => ({ ...c, headerTooltip: c.headerTooltip ?? c.headerName, ...(c.children ? { children: withHeaderTips(c.children) } : {}) }))
}
const LS_BOOK = 'sheet2.predBook.v1'
const lsResultOnly = (t: TabId) => `sheet2.resultOnly.v1.${t}`
const readBool = (k: string) => {
  try {
    return localStorage.getItem(k) === '1'
  } catch {
    return false
  }
}

export default function SheetPage() {
  const [sp, setSp] = useSearchParams()
  const tabParam = sp.get('tab') as TabId | null
  const tab: TabId = tabParam && ALL_TABS.includes(tabParam) ? tabParam : 'predict'
  const setTab = (t: TabId) => {
    const n = new URLSearchParams(sp)
    n.set('tab', t)
    setSp(n, { replace: true })
  }
  const modeParam = sp.get('data') as DataMode | null
  const [mode, setMode] = useState<DataMode>(modeParam === 'api' || modeParam === 'mock' || modeParam === 'both' ? modeParam : 'api')
  const [dbSource, setDbSource] = useDbSource()
  const [range, setRange] = useState<[Dayjs, Dayjs]>(() => {
    // 网址可带 from、to（YYYY-MM-DD）指定日期范围，便于核对和截图
    const f = sp.get('from'), t = sp.get('to')
    return f && t && dayjs(f).isValid() && dayjs(t).isValid() ? [dayjs(f), dayjs(t)] : [dayjs().subtract(6, 'day'), dayjs()]
  })
  const [rows, setRows] = useState<Row[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [predBook, setPredBookState] = useState<Book>(() => {
    try {
      const v = localStorage.getItem(LS_BOOK) as Book | null
      return v && (BOOKS as readonly string[]).includes(v) ? v : 'pinnacle'
    } catch {
      return 'pinnacle'
    }
  })
  const setPredBook = (b: Book) => {
    setPredBookState(b)
    try {
      localStorage.setItem(LS_BOOK, b)
    } catch {
      /* 存不了就只在本页生效 */
    }
  }
  const [resultOnly, setResultOnly] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(Object.keys(CMP_MARKET).map((t) => [t, readBool(lsResultOnly(t as TabId))])),
  )
  const toggleResultOnly = (t: TabId, v: boolean) => {
    setResultOnly((s) => ({ ...s, [t]: v }))
    try {
      localStorage.setItem(lsResultOnly(t), v ? '1' : '0')
    } catch {
      /* 同上 */
    }
  }

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setRows(await loadRows(mode, dbSource, range[0].format('YYYY-MM-DD'), range[1].format('YYYY-MM-DD'), DEFAULT_STRATEGY))
    } catch (e) {
      setRows([])
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [mode, dbSource, range])
  useEffect(() => {
    void load()
  }, [load])

  const lockedBook: Book | null = tab === 'avg' ? 'avg' : (['macau', 'crown', 'pinnacle', 'william', 'bet365'] as string[]).includes(tab) ? (tab as Book) : null
  const settleBook: Book = lockedBook ?? predBook
  const cmpMarket = CMP_MARKET[tab]
  const ro = cmpMarket ? !!resultOnly[tab] : false

  const columnDefs = useMemo(() => {
    const cols = [...baseCols()]
    if (tab === 'predict') cols.push(predCols(settleBook, ['ah', 'ou', 'x1x2', 'jc']))
    else if (tab === 'jc') cols.push(predCols(settleBook, ['jc']), jcCols())
    else if (cmpMarket) {
      cols.push(predCols(settleBook, [cmpMarket]))
      for (const b of orderBooks(CMP_BOOKS)) cols.push(bookCols(b, [cmpMarket], ro))
      if (cmpMarket === 'x1x2') cols.push(jcCols(true, true))
    } else if (lockedBook) cols.push(predCols(lockedBook, ['ah', 'ou', 'x1x2', 'jc']), bookCols(lockedBook))
    return withHeaderTips(cols)
  }, [tab, settleBook, cmpMarket, ro, lockedBook, rows])

  const showBookSelect = tab === 'predict' || !!cmpMarket
  const mockCount = rows.filter((r) => r._mock).length

  return (
    <div className="sheet2">
      <Space wrap size={12} style={{ marginBottom: 8 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          数据表
        </Typography.Title>
        <RangePicker value={range} onChange={(v) => v?.[0] && v[1] && setRange([v[0], v[1]])} allowClear={false} disabled={mode === 'mock'} />
        <DataSourceSwitch value={dbSource} onChange={setDbSource} />
        <Space size={4}>
          <Typography.Text>数据</Typography.Text>
          <Segmented<DataMode>
            value={mode}
            onChange={setMode}
            options={[
              { value: 'api', label: '接口' },
              { value: 'mock', label: '模拟' },
              { value: 'both', label: '接口+模拟' },
            ]}
          />
          <HelpTip tip={'第 1 期调试用。\n接口：只用批量接口的数据，接口还没提供的字段显示「无法获取」。\n模拟：只用前端模拟数据。\n接口+模拟：两者都列出。'} />
        </Space>
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
          刷新
        </Button>
        {mockCount > 0 && <Tag color="purple">模拟数据 {mockCount} 场</Tag>}
      </Space>

      <Space wrap size={16} style={{ marginBottom: 8 }} className="sheet2-tabs">
        <span>
          <span className="sheet2-group-label">数据概览</span>
          <Segmented<TabId> value={OVERVIEW.some((t) => t.value === tab) ? tab : ('' as TabId)} onChange={setTab} options={OVERVIEW} />
        </span>
        <span>
          <span className="sheet2-group-label">机构数据</span>
          <Segmented<TabId> value={BOOK_TABS.some((t) => t.value === tab) ? tab : ('' as TabId)} onChange={setTab} options={BOOK_TABS} />
        </span>
      </Space>

      <Space wrap size={16} style={{ marginBottom: 8, minHeight: 32 }}>
        {showBookSelect ? (
          <Space size={4}>
            <Typography.Text>结算机构</Typography.Text>
            <Select<Book> value={predBook} onChange={setPredBook} style={{ width: 120 }} options={BOOKS.map((b) => ({ value: b, label: BOOK_NAME[b] }))} />
            <HelpTip tip={'只改变结算用的盘口和水位，预测方向不变。\n所选机构没有临盘时按澳门临盘结算，结果格标「按澳门结算」。'} />
          </Space>
        ) : (
          lockedBook && <Tag>结算机构：{BOOK_NAME[lockedBook]}</Tag>
        )}
        {cmpMarket && (
          <Checkbox checked={ro} onChange={(e) => toggleResultOnly(tab, e.target.checked)}>
            仅看赛果
          </Checkbox>
        )}
      </Space>

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 8 }} />}
      {!error && !loading && rows.length === 0 && <Alert type="info" showIcon message="所选日期内没有比赛数据。" style={{ marginBottom: 8 }} />}

      <div style={{ height: 'calc(100vh - 230px)', minHeight: 420 }}>
        <AgGridReact<Row>
          theme={themeQuartz.withParams({ fontSize: 13, headerFontSize: 13, spacing: 5, rowHeight: 32, headerHeight: 34 })}
          localeText={AG_GRID_LOCALE_CN}
          rowData={rows}
          columnDefs={columnDefs}
          getRowId={(p) => (p.data._mock ? 'mock:' : '') + p.data.match_id}
          defaultColDef={{ resizable: true, sortable: true, suppressHeaderMenuButton: true }}
          loading={loading}
          tooltipShowDelay={300}
        />
      </div>
    </div>
  )
}
