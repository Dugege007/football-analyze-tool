// 赛事待确认：接口布尔优先；缺席时用来源名和规范名兜底。列表没有规范名时改用赛事编号。
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import { compileWithTypeScript } from './compile-with-tsc.mjs'

const out = mkdtempSync(join(tmpdir(), 'league-pending-'))
compileWithTypeScript([
  'src/names/leaguePending.ts',
  '--outDir', out,
  '--module', 'es2022',
  '--target', 'es2022',
  '--skipLibCheck',
  '--ignoreConfig',
])
writeFileSync(join(out, 'package.json'), '{"type":"module"}')
const pending = await import(pathToFileURL(join(out, 'leaguePending.js')).href)

test('接口 league_pending_confirm 为 true 或 false 时，以接口为准', () => {
  assert.equal(pending.isLeaguePending('英超', '英超', true), true)
  assert.equal(pending.isLeaguePending('英超', null, true), true)
  assert.equal(pending.isLeaguePending('', null, true), true)
  assert.equal(pending.isLeaguePending('地方联赛', null, false), false)
  assert.equal(pending.isLeaguePending('地方联赛', '', false), false)
  assert.equal(pending.isLeaguePending(null, null, false), false)
})

test('接口字段缺席时，定稿常用名不标星，名册外的来源名才待确认', () => {
  assert.equal(pending.isLeaguePending('地方联赛', null, undefined), true)
  assert.equal(pending.isLeaguePending('地方联赛', '', undefined), true)
  assert.equal(pending.isLeaguePending('地方联赛', '   ', undefined), true)
  assert.equal(pending.isLeaguePending('地方联赛', null, null), true)
  assert.equal(pending.isLeaguePending('  地方联赛  ', null, undefined), true)
  assert.equal(pending.isLeaguePending('英超', null, undefined), false)
  assert.equal(pending.isLeaguePending('英格兰超级联赛', null, undefined), false)
  assert.equal(pending.isLeaguePending('日本J联赛', '', undefined), false)
  assert.equal(pending.isLeaguePending('德国甲级联赛', null, null), false)
})

test('已经升格的常用名不标待确认，临时名继续标星', () => {
  assert.equal(pending.isLeaguePending(null, null, undefined), false)
  assert.equal(pending.isLeaguePending('', null, undefined), false)
  assert.equal(pending.isLeaguePending('   ', null, undefined), false)
  assert.equal(pending.isLeaguePending(undefined, undefined, undefined), false)
  assert.equal(pending.isLeaguePending('英超', '英超', undefined), false)
  assert.equal(pending.isLeaguePending('英格兰超级联赛', ' 英超 ', undefined), false)
  assert.equal(pending.isLeaguePending(null, '英超', undefined), false)
  assert.equal(pending.isLeaguePending('地方联赛', '英超', undefined), false)
  assert.equal(pending.isLeaguePending('地方联赛', '临时联赛', undefined), true)
  assert.equal(pending.isLeaguePending(null, '临时联赛', undefined), true)
})

test('列表和详情没有规范名时，赛事编号不能单独取消星号', () => {
  assert.equal(pending.leagueCanonicalForPending(undefined, 12), '12')
  assert.equal(pending.leagueCanonicalForPending(undefined, null), null)
  assert.equal(pending.leagueCanonicalForPending(undefined, undefined), null)
  assert.equal(pending.leagueCanonicalForPending(undefined, '  '), null)
  assert.equal(pending.leagueCanonicalForPending(null, 12), null)
  assert.equal(pending.leagueCanonicalForPending('', 12), null)
  assert.equal(pending.leagueCanonicalForPending('  芬超  ', null), '芬超')
  assert.equal(pending.isLeaguePending('地方联赛', pending.leagueCanonicalForPending(undefined, null), undefined), true)
  assert.equal(pending.isLeaguePending('地方联赛', pending.leagueCanonicalForPending(undefined, 3), undefined), true)
  assert.equal(pending.isLeaguePending('英超', pending.leagueCanonicalForPending(undefined, 3), undefined), false)
  assert.equal(pending.isLeaguePending('英超', pending.leagueCanonicalForPending(undefined, null), false), false)
})
