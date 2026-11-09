/** 赛程缺口在表格上的短文案和悬停。原因以接口的 schedule_note 为准，这里不另猜。 */

export type ScheduleStatus = 'postponed' | 'cancelled' | 'other'

export const SCHEDULE_CELL_LABEL: Record<string, string> = {
  schedule_postponed: '推迟',
  schedule_cancelled: '取消',
  schedule_other: '其他',
}

export const SCHEDULE_STATUS_LABEL: Record<ScheduleStatus, string> = {
  postponed: '推迟',
  cancelled: '取消',
  other: '其他',
}

/** 北京时间，精确到分钟。接口已经给了 schedule_note 时不再用这个句子顶替。 */
export function formatShanghai(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const bj = new Date(d.getTime() + 8 * 3600e3)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${bj.getUTCFullYear()}-${p(bj.getUTCMonth() + 1)}-${p(bj.getUTCDate())} ${p(bj.getUTCHours())}:${p(bj.getUTCMinutes())}`
}

export function scheduleCellLabel(reason: string | null | undefined): string | null {
  if (!reason) return null
  return SCHEDULE_CELL_LABEL[reason] ?? null
}

export function scheduleSentence(m: {
  schedule_status?: string | null
  schedule_note?: string | null
  kickoff_actual?: string | null
}): string | null {
  if (m.schedule_note) return m.schedule_note
  if (m.schedule_status === 'postponed' && m.kickoff_actual) {
    return `推迟至 ${formatShanghai(m.kickoff_actual)}（北京时间）`
  }
  if (m.schedule_status === 'postponed') return '比赛推迟，新的开赛时间尚未确认'
  if (m.schedule_status === 'cancelled') return '比赛取消'
  if (m.schedule_status === 'other') return '已从竞彩列表消失，不能确定是推迟、取消还是其他原因'
  return null
}

/** 开赛格悬停。有推迟时间时先写推迟至何时，再写变更次数。 */
export function kickoffHoverLines(m: {
  schedule_status?: string | null
  schedule_note?: string | null
  kickoff_actual?: string | null
  kickoff_rev?: number | null
}): string[] {
  const lines: string[] = []
  const sentence = scheduleSentence(m)
  if (sentence) lines.push(sentence)
  if (typeof m.kickoff_rev === 'number' && m.kickoff_rev > 0) {
    lines.push(`开赛时间曾变更（第 ${m.kickoff_rev} 次）`)
  }
  return lines
}
