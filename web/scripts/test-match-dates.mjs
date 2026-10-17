// 日历置灰：有比赛只看日期集合；月份未返回时不算无比赛；范围选择器要带上下一个月。
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import { compileWithTypeScript } from './compile-with-tsc.mjs'

const out = mkdtempSync(join(tmpdir(), 'match-dates-'))
compileWithTypeScript(['src/api/matchDateMarks.ts',
  '--outDir', out, '--module', 'es2022', '--target', 'es2022', '--skipLibCheck', '--ignoreConfig'])
writeFileSync(join(out, 'package.json'), '{"type":"module"}')
const M = await import(pathToFileURL(join(out, 'matchDateMarks.js')).href)

const juneSample = new Set(['2026-06-01', '2026-06-02', '2026-06-04', '2026-06-05'])

test('六月闭区间是 1 日到 30 日，二月按平闰年取最后一天', () => {
  assert.deepEqual(M.monthRange('2026-06'), { from: '2026-06-01', to: '2026-06-30' })
  assert.deepEqual(M.monthRange('2026-02'), { from: '2026-02-01', to: '2026-02-28' })
  assert.deepEqual(M.monthRange('2024-02'), { from: '2024-02-01', to: '2024-02-29' })
  assert.equal(M.monthRange('2026-13'), null)
})

test('缓存键把范围和月份分开', () => {
  assert.equal(M.matchDateCacheKey('jingcai', '2026-06'), 'jingcai|2026-06')
  assert.notEqual(M.matchDateCacheKey('jingcai', '2026-06'), M.matchDateCacheKey('extra', '2026-06'))
  assert.notEqual(M.matchDateCacheKey('all', '2026-06'), M.matchDateCacheKey('jingcai', '2026-06'))
})

test('契约示例：06-03 无比赛，06-01 有比赛；空数组表示整月都没有', () => {
  assert.equal(M.classifyJingcaiDay(juneSample, '2026-06-01'), 'has')
  assert.equal(M.classifyJingcaiDay(juneSample, '2026-06-03'), 'none')
  assert.equal(M.classifyJingcaiDay(juneSample, '2026-06-07'), 'none')
  assert.equal(M.classifyJingcaiDay(new Set(), '2026-07-01'), 'none')
  assert.equal(M.classifyJingcaiDay(undefined, '2026-06-03'), 'unknown')
})

test('单月面板只取锚点月，双月面板再取下一个月，十二月跨年', () => {
  assert.deepEqual(M.monthsForDatePanel('2026-06', 1), ['2026-06'])
  assert.deepEqual(M.monthsForDatePanel('2026-06', 2), ['2026-06', '2026-07'])
  assert.deepEqual(M.monthsForDatePanel('2026-12', 2), ['2026-12', '2027-01'])
  assert.deepEqual(M.monthsForDatePanel('bad', 2), [])
})
