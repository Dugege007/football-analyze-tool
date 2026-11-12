import type { ReactNode } from 'react'
import { Tooltip } from 'antd'
import {
  redCardBadgeAnchor,
  redCardCountOf,
  redCardCountTip,
  visibleRedCardCount,
  type RedCardResult,
  type RedCardSide,
} from './redCardBadgeLogic'

/**
 * 队名上的红牌角标。
 * 主队放在队名左上角，客队放在队名右上角。
 * 张数小于 1、还没取到、或字段缺席时不渲染角标，队名、别名悬停和待确认星号保持原样。
 */
export function TeamNameWithRedCard({
  side,
  result,
  children,
}: {
  side: RedCardSide
  result?: RedCardResult | null
  children: ReactNode
}) {
  const count = visibleRedCardCount(redCardCountOf(result, side))
  if (count == null) return children
  const badge = <RedCardMark side={side} count={count} />
  const anchor = redCardBadgeAnchor(side)
  return (
    <span className={`team-name-with-card team-name-with-card-${side}`}>
      {anchor === 'start' ? badge : null}
      {children}
      {anchor === 'end' ? badge : null}
    </span>
  )
}

/** 红色数字本身。悬停只写出该队红牌张数。 */
function RedCardMark({ side, count }: { side: RedCardSide; count: number }) {
  const tip = redCardCountTip(count)
  return (
    <Tooltip title={tip}>
      <span className={`red-card-badge red-card-badge-${side}`} aria-label={tip}>
        {count}
      </span>
    </Tooltip>
  )
}
