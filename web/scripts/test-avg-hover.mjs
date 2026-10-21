/**
 * 跑多家平均汇总面板样例。样例写在 test-avg-hover.ts，这里只负责用开发服务器的模块加载把它执行掉。
 */
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
try {
  await server.ssrLoadModule('/scripts/test-avg-hover.ts')
} finally {
  await server.close()
}
