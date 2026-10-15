import { Typography } from 'antd'
import type { TeamLabel } from '../names/matchLabels'
import { TeamPendingMark } from './TeamPendingMark'

const { Text } = Typography

/**
 * 按数据表同一套规则显示赛事简称。
 * 格子里是简称，悬停是来源赛事名。
 */
export function DisplayLeague({
  name,
  title,
  empty = '—',
}: {
  name: string | null
  title?: string
  empty?: string
}) {
  return <span title={title}>{name ?? empty}</span>
}

/**
 * 按数据表同一套规则显示一支球队。
 * 格子里是常用中文名，悬停列出其他名称，待确认时在队名旁加星号。
 */
export function DisplayTeam({
  label,
  strong = false,
  empty = '—',
}: {
  label: TeamLabel
  strong?: boolean
  empty?: string
}) {
  const body = (
    <>
      {label.text ?? empty}
      {label.pending ? <TeamPendingMark /> : null}
    </>
  )
  if (strong) {
    return (
      <Text strong title={label.tip}>
        {body}
      </Text>
    )
  }
  return <span title={label.tip}>{body}</span>
}
