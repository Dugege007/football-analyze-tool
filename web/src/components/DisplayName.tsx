import type { ReactNode } from 'react'
import { Tooltip, Typography } from 'antd'
import type { TeamLabel } from '../names/matchLabels'
import { PendingMark, TeamPendingMark } from './TeamPendingMark'

const { Text } = Typography

/**
 * 赛事名与队名共用的悬停。
 * tip 是别名列表，一行一个，不加前缀。没有文案时不包 Tooltip。
 */
export function NameHover({ tip, children }: { tip?: string; children: ReactNode }) {
  if (!tip) return <>{children}</>
  return (
    <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{tip}</div>}>
      <span>{children}</span>
    </Tooltip>
  )
}

/**
 * 按数据表同一套规则显示赛事简称。
 * 格子里是简称。有别名时悬停一行一个；没有别名时悬停仍是来源名。
 * 待确认时在简称旁加星号，星号悬停仍是「待确认」。
 */
export function DisplayLeague({
  name,
  title,
  pending = false,
  empty = '-',
}: {
  name: string | null
  title?: string
  pending?: boolean
  empty?: string
}) {
  return (
    <span>
      <NameHover tip={title}>{name ?? empty}</NameHover>
      {pending ? <PendingMark /> : null}
    </span>
  )
}

/**
 * 按数据表同一套规则显示一支球队。
 * 格子里是常用中文名。有别名时悬停一行一个，不加前缀。待确认时在队名旁加星号。
 */
export function DisplayTeam({
  label,
  strong = false,
  empty = '-',
}: {
  label: TeamLabel
  strong?: boolean
  empty?: string
}) {
  const marked = (
    <>
      <NameHover tip={label.tip}>{label.text ?? empty}</NameHover>
      {label.pending ? <TeamPendingMark /> : null}
    </>
  )
  if (strong) return <Text strong>{marked}</Text>
  return <span>{marked}</span>
}
