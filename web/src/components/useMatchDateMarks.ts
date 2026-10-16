/**
 * 赛程和数据表的日期面板：按打开的月份请求 GET /matches/dates，并给没有比赛的当月日期加样式类。
 * 只做视觉区分，不禁用日期。接口失败或尚未返回时不加样式类。
 */
import { cloneElement, isValidElement, useCallback, useEffect, useRef, useState, type ReactElement, type ReactNode } from 'react'
import type { Dayjs } from 'dayjs'
import { listMatchDates } from '../api/client'
import {
  classifyJingcaiDay,
  matchDateCacheKey,
  monthRange,
  monthsForDatePanel,
  type JingcaiDayMark,
} from '../api/matchDateMarks'

const NO_DATA_CLASS = 'picker-day-no-data'

const monthCache = new Map<string, Set<string>>()
const inflight = new Map<string, Promise<void>>()

type Anchor = { month: string; panelCount: 1 | 2 }

function isoDateOf(current: unknown): string | null {
  if (typeof current !== 'object' || current === null || !('format' in current)) return null
  const format = (current as { format?: unknown }).format
  if (typeof format !== 'function') return null
  const text = format.call(current, 'YYYY-MM-DD')
  return typeof text === 'string' ? text : null
}

/**
 * 当月、且该月结果已经返回、且这一天不在结果里时，给格子加上无比赛样式。
 * 上月、下月的格子是否在视图内由样式表用 ant-picker-cell-in-view 再限一次，这里不改禁用状态。
 */
export function renderJingcaiDateCell(
  current: unknown,
  info: { type: string; originNode: ReactNode },
  markOf: (isoDate: string) => JingcaiDayMark,
): ReactNode {
  if (info.type !== 'date' || !isValidElement(info.originNode)) return info.originNode
  const isoDate = isoDateOf(current)
  if (!isoDate || markOf(isoDate) !== 'none') return info.originNode
  const node = info.originNode as ReactElement<{ className?: string }>
  const prev = node.props.className ?? ''
  return cloneElement(node, { className: `${prev} ${NO_DATA_CLASS}`.trim() })
}

/** 按 scope 缓存每个月有比赛的竞彩日。面板打开、换月或范围变化时补齐还没有的月份。 */
export function useMatchDateMarks(scope: string) {
  const [, setTick] = useState(0)
  const anchorRef = useRef<Anchor | null>(null)

  const ensureMonths = useCallback((months: string[]) => {
    const tasks: Promise<void>[] = []
    for (const month of months) {
      const key = matchDateCacheKey(scope, month)
      if (monthCache.has(key)) continue
      const range = monthRange(month)
      if (!range) continue
      let task = inflight.get(key)
      if (!task) {
        task = listMatchDates(range.from, range.to, scope)
          .then((dates) => {
            if (dates) monthCache.set(key, new Set(dates))
          })
          .finally(() => {
            inflight.delete(key)
          })
        inflight.set(key, task)
      }
      tasks.push(task)
    }
    if (tasks.length === 0) return
    void Promise.allSettled(tasks).then(() => {
      setTick((n) => n + 1)
    })
  }, [scope])

  useEffect(() => {
    const anchor = anchorRef.current
    if (!anchor) return
    ensureMonths(monthsForDatePanel(anchor.month, anchor.panelCount))
  }, [ensureMonths])

  const syncAnchor = useCallback((anchor: Dayjs, panelCount: 1 | 2) => {
    const month = anchor.format('YYYY-MM')
    anchorRef.current = { month, panelCount }
    ensureMonths(monthsForDatePanel(month, panelCount))
  }, [ensureMonths])

  const markOf = useCallback((isoDate: string): JingcaiDayMark => {
    return classifyJingcaiDay(monthCache.get(matchDateCacheKey(scope, isoDate.slice(0, 7))), isoDate)
  }, [scope])

  return { syncAnchor, markOf }
}
