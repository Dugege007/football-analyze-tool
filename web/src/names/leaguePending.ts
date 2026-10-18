/**
 * 赛事是否还要在名称旁标「待确认」。
 * 规则见 docs/schema/v2_0-name-canonical-and-star-rules.md，与球队共用。
 * 接口给出布尔值时以接口为准。
 * 字段缺席时：来源名或规范名已经是升格后的常用名则不标星。
 * 临时名、以及还没有常用名的来源名，继续标星。赛事编号不是升格。
 * 没有来源名、也没有规范名时不标星。
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
  const mapped = typeof canonical === 'string' ? canonical.trim() : ''
  if (!source && !mapped) return false
  if (leagueNameEstablished(source) || leagueNameEstablished(mapped)) return false
  return true
}

/**
 * 列表和详情传给 isLeaguePending 的规范名。返回值只用来判断是否已映射，不在页面上展示。
 * 规范名已经出现在响应里时，以去掉空白后的规范名为准；空字符串视为没有规范名。
 * 规范名缺席时仍可带回赛事编号，但编号本身不是常用名，不能单独取消星号。
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
