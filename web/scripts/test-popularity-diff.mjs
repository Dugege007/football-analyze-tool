// 人气差值只做显示换算：小数乘以 100，保留 1 位小数；空值不显示成数字。
import { execFileSync } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'

const out = mkdtempSync(join(tmpdir(), 'popularity-diff-'))
execFileSync('node_modules/.bin/tsc', ['src/api/popularity.ts', 'src/api/dataSource.ts',
  '--outDir', out, '--module', 'es2022', '--target', 'es2022', '--skipLibCheck', '--ignoreConfig'], { stdio: 'inherit' })
writeFileSync(join(out, 'package.json'), '{"type":"module"}')
const F = await import(pathToFileURL(join(out, 'popularity.js')).href)
const D = await import(pathToFileURL(join(out, 'dataSource.js')).href)

/** 与对比页相同：只读，或 meta.db 明确不是 live 时锁定。 */
function compareLocked(meta, fetchFailed) {
  const bad = D.metaMismatch(meta)
  return meta?.readonly === true || fetchFailed || bad != null
}

test('0.053 显示为 5.3%', () => {
  assert.equal(F.formatPopularityDiff(0.053), '5.3%')
})
test('负数保留符号', () => {
  assert.equal(F.formatPopularityDiff(-0.126), '-12.6%')
})
test('空值不换算成数字', () => {
  assert.equal(F.formatPopularityDiff(null), null)
  assert.equal(F.formatPopularityDiff(undefined), null)
  assert.equal(F.formatPopularityDiff(Number.NaN), null)
})

test('正常 live 响应不锁页面', () => {
  const meta = { db: 'live', promoted: true, readonly: false }
  assert.equal(D.metaMismatch(meta), null)
  assert.equal(compareLocked(meta, false), false)
  assert.equal(compareLocked({ db: ' Live ', promoted: true, readonly: false }, false), false)
})

test('没有 meta.db 不因库标识锁页面', () => {
  assert.equal(D.metaMismatch(null), null)
  assert.equal(D.metaMismatch(undefined), null)
  assert.equal(D.metaMismatch({}), null)
  assert.equal(D.metaMismatch({ db: null, readonly: false }), null)
  assert.equal(D.metaMismatch({ db: '  ', readonly: false }), null)
  assert.equal(compareLocked({ promoted: true, readonly: false }, false), false)
})

test('响应明确不是 live 时才因库标识锁定', () => {
  const meta = { db: 'v2d3', promoted: false, readonly: true }
  assert.match(D.metaMismatch(meta), /不是正式库/)
  assert.equal(compareLocked(meta, false), true)
  assert.equal(compareLocked({ db: 'live', readonly: true }, false), true)
})
