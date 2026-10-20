/**
 * 基本面格子的色字。进失对必须拆成进、斜杠、失三段，不能把一个色类加在整串上。
 * 缺测「—」与零值不上色。
 */
import { Tooltip } from 'antd'
import type { ReactNode } from 'react'
import { FUND_DASH, splitGfGa, type SideTone } from './fundamentals'

const TONE_CLASS: Record<Exclude<SideTone, 'plain'>, string> = {
  orange: 't-orange',
  blue: 't-blue',
}

function withTip(body: ReactNode, tip?: string | null): ReactNode {
  if (!tip) return body
  return <Tooltip title={tip}><span>{body}</span></Tooltip>
}

/** 进红、斜杠默认、失绿。不是进失对时原样输出，不加色。 */
export function GfGaText({ text, tip }: { text: string; tip?: string | null }): ReactNode {
  const parts = splitGfGa(text)
  const body = parts ? (
    <>
      <span className="t-red">{parts.gf}</span>/<span className="t-green">{parts.ga}</span>
    </>
  ) : (
    text
  )
  return withTip(body, tip)
}

/** 侧别色只加在有数值的格子上。缺测保持正文。 */
export function TonedText({ text, tone, tip }: { text: string; tone: SideTone; tip?: string | null }): ReactNode {
  const cls = tone === 'plain' || text === FUND_DASH ? '' : TONE_CLASS[tone]
  const body = cls ? <span className={cls}>{text}</span> : text
  return withTip(body, tip)
}
