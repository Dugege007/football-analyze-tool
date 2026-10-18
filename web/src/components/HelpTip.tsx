import type { ReactNode } from 'react'
import { Tooltip } from 'antd'
import { QuestionCircleOutlined } from '@ant-design/icons'

/**
 * 界面规范（见 web/UI-GUIDELINES.md 规则一、规则二）：注释性说明不直接写在页面上。
 * 问号悬停只说明数据来源或如何使用，不写业务口径、程序逻辑和内部规则。
 */
export function HelpTip({ tip }: { tip: ReactNode }) {
  return (
    <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{tip}</div>}>
      <QuestionCircleOutlined
        aria-label="说明"
        style={{ marginLeft: 4, color: 'rgba(0,0,0,0.45)', cursor: 'help', fontSize: 13 }}
        onClick={(e) => e.stopPropagation()}
      />
    </Tooltip>
  )
}

/** 标签文字加问号说明。 */
export function LabelWithHelp({ label, tip }: { label: ReactNode; tip: ReactNode }) {
  return (
    <span style={{ whiteSpace: 'nowrap' }}>
      {label}
      <HelpTip tip={tip} />
    </span>
  )
}

export default HelpTip
