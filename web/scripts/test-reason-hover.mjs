/**
 * 跑六009 悬停样例。样例写在 test-reason-hover.ts，这里只负责用开发服务器的模块加载把它执行掉。
 */
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
try {
  await server.ssrLoadModule('/scripts/test-reason-hover.ts')
} finally {
  await server.close()
}
