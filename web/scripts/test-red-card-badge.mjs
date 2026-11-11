/**
 * 跑红牌角标样例。样例写在 test-red-card-badge.ts，这里用开发服务器的模块加载执行。
 */
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
try {
  await server.ssrLoadModule('/scripts/test-red-card-badge.ts')
} finally {
  await server.close()
}
