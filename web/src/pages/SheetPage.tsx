/**
 * 数据表页（/sheet）第 1 期：按用户 2026-10-14 修订的《数据表页面意见》重做。
 * 数据概览：预测、基本面、三项对照。机构数据：多家平均、五家机构、竞彩。
 * 字段名照后端《数据表接口字段约定（第 1 期）》；盘口格子缺字段显示「无法获取」。
 * 基本面 xG 缺字段显示「-」。页面不写解释性文字，说明放在小问号里（web/UI-GUIDELINES.md）。
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
import { renderJingcaiDateCell, useMatchDateMarks } from '../components/useMatchDateMarks'
import { DEFAULT_STRATEGY } from '../api/strategies'
import { apiBooks, loadRows, type DataMode } from '../sheet2/load'
import { SHEET_RANGE_PRESETS, clampSheetYmdRange, currentInstantMs, matchingSheetRangePresets, sheetRangePreset, type SheetRangePresetId } from '../sheet2/dateRangePresets'
import { ALL_SHEET_TABS, BOOK_TABS, buildSheetColumns, CMP_MARKET, OVERVIEW_TABS, type SheetTabId } from '../sheet2/tabColumns'
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

type TabId = SheetTabId
const OVERVIEW = OVERVIEW_TABS
const ALL_TABS: TabId[] = ALL_SHEET_TABS

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
  const [clockMs, setClockMs] = useState(() => Date.now())
  const [range, setRange] = useState<[Dayjs, Dayjs]>(() => {
    // 网址可带 from、to（YYYY-MM-DD）指定日期范围，便于核对和截图。缺省与「近7日」相同，按北京时间取今天往前共 7 天。
    // 带入的区间日历差超过 31 天时，先把结束日收到起始日加 31 天，首屏请求才不会被接口拒绝。
    const f = sp.get('from'), t = sp.get('to')
    if (f && t && dayjs(f).isValid() && dayjs(t).isValid()) {
      const clamped = clampSheetYmdRange(dayjs(f).format('YYYY-MM-DD'), dayjs(t).format('YYYY-MM-DD'))
      return [dayjs(clamped.from), dayjs(clamped.to)]
    }
    const last7 = sheetRangePreset('last7', Date.now())
    return [dayjs(last7.from), dayjs(last7.to)]
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
      setRows(await loadRows(mode, range[0].format('YYYY-MM-DD'), range[1].format('YYYY-MM-DD'), DEFAULT_STRATEGY))
    } catch (e) {
      setRows([])
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [mode, range])
  useEffect(() => {
    void load()
  }, [load])
  useEffect(() => {
    const timer = setInterval(() => setClockMs(currentInstantMs()), 60 * 1000)
    return () => clearInterval(timer)
  }, [])

  const lockedBook: Book | null = (['avg', 'macau', 'crown', 'pinnacle', 'william', 'bet365'] as string[]).includes(tab) ? (tab as Book) : null
  const settleBook: Book = lockedBook ?? predBook
  const cmpMarket = CMP_MARKET[tab]
  const ro = cmpMarket ? !!resultOnly[tab] : false

  const columnDefs = useMemo(() => {
    return withHeaderTips(buildSheetColumns({ tab, settleBook, resultOnly: ro, orderBooks }))
  }, [tab, settleBook, ro, rows])

  const showBookSelect = tab === 'predict' || tab === 'fundamentals' || !!cmpMarket
  const mockCount = rows.filter((r) => r._mock).length
  // 数据表只看竞彩场，不跟赛程页的范围切换。
  const matchDates = useMatchDateMarks('jingcai')
  // 手动选的范围和快捷按钮都只改这一处，加载效果跟着 range 走。
  // 确认后的日历差超过 31 天时，结束日改为起始日加 31 天，起始日不动。
  const applyRange = (nextFrom: Dayjs, nextTo: Dayjs) => {
    const clamped = clampSheetYmdRange(nextFrom.format('YYYY-MM-DD'), nextTo.format('YYYY-MM-DD'))
    setRange([dayjs(clamped.from), dayjs(clamped.to)])
  }
  const applyPreset = (id: SheetRangePresetId) => {
    const at = currentInstantMs()
    setClockMs(at)
    const picked = sheetRangePreset(id, at)
    applyRange(dayjs(picked.from), dayjs(picked.to))
  }
  const activePresetIds = matchingSheetRangePresets(range[0].format('YYYY-MM-DD'), range[1].format('YYYY-MM-DD'), clockMs)

  return (
    <div className="sheet2">
      <Space wrap size={12} style={{ marginBottom: 8 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          数据表
        </Typography.Title>
        <RangePicker
          value={range}
          onChange={(v) => v?.[0] && v[1] && applyRange(v[0], v[1])}
          allowClear={false}
          disabled={mode === 'mock'}
          onOpenChange={(open) => {
            if (open) matchDates.syncAnchor(range[0], 2)
          }}
          onPanelChange={(values, modes) => {
            const anchor = values[0] ?? values[1]
            if (anchor && modes.includes('date')) matchDates.syncAnchor(anchor, 2)
          }}
          onPickerValueChange={(values, info) => {
            const anchor = info.range === 'end' ? values[1] : values[0]
            if (anchor && info.mode.includes('date')) matchDates.syncAnchor(anchor, 2)
          }}
          cellRender={(current, info) => renderJingcaiDateCell(current, info, matchDates.markOf)}
        />
        <Space.Compact>
          {SHEET_RANGE_PRESETS.map((preset) => (
            <Button
              key={preset.id}
              type={activePresetIds.includes(preset.id) ? 'primary' : 'default'}
              disabled={mode === 'mock'}
              onClick={() => applyPreset(preset.id)}
            >
              {preset.label}
            </Button>
          ))}
        </Space.Compact>
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
          <HelpTip tip={'第 1 期调试用。\n接口：只用批量接口的数据。盘口格子缺字段显示「无法获取」。基本面里的 xG 缺字段显示「-」，其余基本面缺字段显示「—」。\n模拟：只用前端模拟数据。\n接口+模拟：两者都列出。'} />
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
            <HelpTip
              tip={
                '只改变结算用的盘口和水位，预测方向不变。\n没有写明结算盘时，平博临盘优先，没有再用澳门。结果格只在接口标明回退时标出实际机构。\n已经冻结的预测仍读该行自己的结算盘。旧方案可能仍是澳门临盘，不要把上面的默认口径当成这些行的结算盘。'
              }
            />
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

      {/* 吃掉筛选行以下的剩余高度，底边贴页脚。筛选行变高时这里跟着缩短。 */}
      <div className="sheet2-grid">
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
