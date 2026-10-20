/**
 * 跑基本面宽表列样例。样例写在 test-fund-columns.ts。
 */
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
try {
  await server.ssrLoadModule('/scripts/test-fund-columns.ts')
} finally {
  await server.close()
}
