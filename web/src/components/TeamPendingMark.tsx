import { Tooltip } from 'antd'

/** 认不出的球队在队名旁显示的星号，悬停说明为「待确认」，样式与数据表一致。 */
export function TeamPendingMark() {
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
