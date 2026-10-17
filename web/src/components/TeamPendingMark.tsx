import { Tooltip } from 'antd'

/** 认不出的赛事或球队在名称旁显示的星号。悬停说明为「待确认」，样式与数据表队名星号一致。 */
export function PendingMark() {
  return (
    <Tooltip title="待确认">
      <span
        className="team-pending-mark"
        style={{ marginLeft: 2, fontSize: 10, color: '#faad14', cursor: 'help', verticalAlign: 'super' }}
      >
        *
      </span>
    </Tooltip>
  )
}

/** 队名旁的待确认星号。与赛事共用 PendingMark，显示效果保持不变。 */
export const TeamPendingMark = PendingMark
