/**
 * 赛果红牌的显示判断。
 * 接口字段是 result.home_red_cards 与 result.away_red_cards。
 * null 表示没取到统计，0 表示确认没有红牌，大于等于 1 的整数才显示角标。
 * 这里不把缺字段、null 或非整数改写成 0，也不根据比分另算张数。
 */

export type RedCardSide = 'home' | 'away'

/** 赛果上可能出现的红牌字段。缺字段时这两个键可以不存在。 */
export interface RedCardResult {
  home_red_cards?: number | null
  away_red_cards?: number | null
}

/**
 * 原样保留接口给出的红牌数。
 * 缺字段保持 undefined，明确的 null 保持 null，0 保持 0。
 * 不是整数的值不改写成 0。
 */
export function passthroughRedCardCount(value: unknown): number | null | undefined {
  if (value === undefined) return undefined
  if (value === null) return null
  if (typeof value === 'number' && Number.isInteger(value)) return value
  return undefined
}

/** 从赛果取出该队红牌数。没有赛果或字段缺席时为 undefined，不补 0。 */
export function redCardCountOf(
  result: RedCardResult | null | undefined,
  side: RedCardSide,
): number | null | undefined {
  if (result == null) return undefined
  return side === 'home' ? result.home_red_cards : result.away_red_cards
}

/**
 * 可以画成角标的张数。
 * 只有大于等于 1 的整数返回原值；0、null、undefined、缺字段和非整数都返回 null。
 */
export function visibleRedCardCount(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1) return null
  return value
}

/** 主队角标在队名左侧，客队角标在队名右侧。配合上标，分别落在左上角和右上角。 */
export function redCardBadgeAnchor(side: RedCardSide): 'start' | 'end' {
  return side === 'home' ? 'start' : 'end'
}

/** 悬停只写这一队的红牌张数。 */
export function redCardCountTip(count: number): string {
  return `红牌 ${count} 张`
}
