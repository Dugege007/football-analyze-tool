// 测试脚本共用：用当前 Node.js 执行本仓库安装的 TypeScript 编译器（tsc）。
// Windows 的 node_modules/.bin 只有 tsc.cmd 与 tsc.ps1，没有可直接执行的无扩展名 tsc。
// execFileSync 若写死 node_modules/.bin/tsc，在 Windows 上会找不到文件。
// 这里解析 typescript 包内的 bin/tsc 脚本，交给当前 node 执行。Linux、macOS 与 Windows 走同一条路径。
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

/**
 * 用本仓库的 TypeScript 编译器编译。工作目录沿用调用方，标准输出与标准错误直接继承。
 * @param {string[]} args 传给 tsc 的参数，包含源文件路径与编译选项。
 */
export function compileWithTypeScript(args) {
  const compiler = require.resolve('typescript/bin/tsc')
  execFileSync(process.execPath, [compiler, ...args], { stdio: 'inherit' })
}
