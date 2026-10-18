/**
 * 赛事是否还要在名称旁标「待确认」。
 * 规则与球队的 isTeamPending 同一层：接口给出布尔值时以接口为准。
 * 接口字段 league_pending_confirm 缺席时，来源名已经是定稿常用名或别名则不标星。
 * 有来源名、规范名为空、且不在定稿常用名里，才标星。
 * 规范名缺席时，赛事编号有值仍表示已经映射。没有来源赛事名时不标星。
 */
import { leagueNameEstablished } from './leagueShort.js'

/**
 * 判断这一场的赛事名是否显示待确认星号。
 * apiPending 是布尔值时直接采用，不再看名称。null 与缺席都走名称兜底。
 */
export function isLeaguePending(
  name: string | null | undefined,
  canonical?: string | null,
  apiPending?: boolean | null,
): boolean {
  if (typeof apiPending === 'boolean') return apiPending
  const source = typeof name === 'string' ? name.trim() : ''
  if (!source) return false
  const mapped = typeof canonical === 'string' ? canonical.trim() : ''
  if (mapped.length > 0) return false
  if (leagueNameEstablished(source)) return false
  return true
}

/**
 * 列表和详情传给 isLeaguePending 的规范名。返回值只用来判断是否已映射，不在页面上展示。
 * 规范名已经出现在响应里时，以去掉空白后的规范名为准；空字符串视为未映射，不再改用赛事编号。
 * 规范名缺席时，赛事编号有值表示已经映射。
 */
export function leagueCanonicalForPending(
  canonical: string | null | undefined,
  leagueId?: number | string | null,
): string | null {
  if (canonical !== undefined) return canonical?.trim() || null
  if (leagueId == null) return null
  const idText = String(leagueId).trim()
  return idText || null
}
