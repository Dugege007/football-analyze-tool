/**
 * 人气差值（支持率差值）只做显示换算。
 * 后端存小数，页面乘以 100 后保留 1 位小数。空值返回 null，由各页面沿用自己的空值文案。
 */
export function formatPopularityDiff(value: number | null | undefined): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  const text = (value * 100).toFixed(1)
  return `${text === '-0.0' ? '0.0' : text}%`
}
