/**
 * 赛事名与队名共用的别名悬停文案。
 * 每条名称单独一行，不加「其他名称」或「别名」前缀。
 * 没有名称时返回 undefined，调用方不渲染空 Tooltip。
 */
export function formatAliasLines(names: Iterable<string>): string | undefined {
  const lines: string[] = []
  const seen = new Set<string>()
  for (const raw of names) {
    const text = raw.trim()
    if (!text || seen.has(text)) continue
    seen.add(text)
    lines.push(text)
  }
  return lines.length ? lines.join('\n') : undefined
}
