/**
 * 正式库接口前缀。开发服务器把 /api 代理到 VITE_API_BASE（默认 http://127.0.0.1:8787）。
 * meta.db 只有在响应里明确给出、且不是 live 时才视为不符。没有 meta.db 不锁页面。
 */
export const apiBase = '/api'

export interface DbMeta {
  db?: string | null
  promoted?: boolean | null
  readonly?: boolean | null
}

/** 取出响应里的 meta.db。没有该字段时返回空串。 */
export function metaDb(meta: DbMeta | null | undefined): string {
  return typeof meta?.db === 'string' ? meta.db.trim().toLowerCase() : ''
}

/**
 * 只在响应明确写出 meta.db 且该值不是 live 时返回中文错误。
 * 正常 live（含首尾空白、大小写差异）、以及没有 meta.db 的响应都返回 null，避免误锁。
 */
export function metaMismatch(meta: DbMeta | null | undefined): string | null {
  const db = metaDb(meta)
  if (!db) return null
  if (db !== 'live') return `后端返回 meta.db=${meta?.db}，不是正式库（live），已停止显示`
  return null
}

export async function fetchHealthMeta(): Promise<DbMeta | null> {
  const res = await fetch(`${apiBase}/health`)
  if (!res.ok) throw new Error(`正式库 /health 返回 HTTP ${res.status}`)
  const body = (await res.json()) as { meta?: DbMeta }
  return body.meta ?? null
}
