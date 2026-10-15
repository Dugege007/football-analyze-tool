/**
 * 正式库接口前缀。开发服务器把 /api 代理到 VITE_API_BASE（默认 http://127.0.0.1:8787）。
 * 只认响应里的 meta.db=live；不是 live 或没有 meta.db 时停止显示。
 */
export const apiBase = '/api'

export interface DbMeta {
  db?: string | null
  promoted?: boolean | null
  readonly?: boolean | null
}

/** meta.db 为 live 时返回 null；否则返回中文错误 */
export function metaMismatch(meta: DbMeta | null | undefined): string | null {
  if (!meta || !meta.db) return '响应里没有 meta.db，无法确认数据来源，已停止显示'
  if (meta.db !== 'live') return `后端返回 meta.db=${meta.db}，不是正式库（live），已停止显示`
  return null
}

export async function fetchHealthMeta(): Promise<DbMeta | null> {
  const res = await fetch(`${apiBase}/health`)
  if (!res.ok) throw new Error(`正式库 /health 返回 HTTP ${res.status}`)
  const body = (await res.json()) as { meta?: DbMeta }
  return body.meta ?? null
}
