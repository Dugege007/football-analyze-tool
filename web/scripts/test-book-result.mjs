/**
 * 跑机构赛果只读样例。样例写在 test-book-result.ts，这里只负责用开发服务器的模块加载把它执行掉。
 */
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
try {
  await server.ssrLoadModule('/scripts/test-book-result.ts')
} finally {
  await server.close()
}
