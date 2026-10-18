// 数据表名称：有规范名时显示规范名，星号以接口布尔为准。别名悬停一行一个，不加前缀。
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import { build } from 'vite'
import { compileWithTypeScript } from './compile-with-tsc.mjs'

const out = mkdtempSync(join(tmpdir(), 'sheet-names-'))
const labelsOut = join(out, 'labels')
await build({
  logLevel: 'silent',
  ssr: { noExternal: true },
  build: {
    ssr: 'src/sheet/adapter.ts',
    outDir: join(out, 'bundle'),
    emptyOutDir: true,
    rollupOptions: { output: { format: 'es', entryFileNames: 'adapter.js' } },
  },
})
compileWithTypeScript([
  'src/names/matchLabels.ts',
  '--outDir', labelsOut,
  '--rootDir', 'src',
  '--module', 'es2022',
  '--target', 'es2022',
  '--moduleResolution', 'bundler',
  '--skipLibCheck',
  '--ignoreConfig',
])
writeFileSync(join(labelsOut, 'package.json'), '{"type":"module"}')
const adapter = await import(pathToFileURL(join(out, 'bundle/adapter.js')).href)
const { labelsForTableMatch } = await import(pathToFileURL(join(labelsOut, 'names/matchLabels.js')).href)
process.on('exit', () => {
  rmSync(out, { recursive: true, force: true })
})

function backendRow(match) {
  return {
    match_id: '2026-10-18|六001',
    phase_exception: false,
    match: {
      match_id: '2026-10-18|六001',
      jc_id: '六001',
      jc_no: 1,
      jingcai_date: '2026-10-18',
      kickoff_at: null,
      scope: 'jingcai',
      league: '沙特联',
      ...match,
    },
  }
}

test('数据表适配器优先显示规范名，星号使用待确认布尔值', () => {
  const row = adapter.normalizeTableRow(adapter.fromBackendRow(backendRow({
    league_canonical: '沙特阿拉伯职业联赛',
    league_pending_confirm: false,
    home_team: '利雅得希拉尔',
    away_team: '金堡尔',
    home_team_canonical: '利雅得新月',
    away_team_canonical: '坎布尔',
    home_team_pending_confirm: false,
    away_team_pending_confirm: true,
  })))
  assert.equal(row.league, '沙特联')
  assert.equal(row.leaguePending, false)
  assert.equal(row.home, '利雅得新月')
  assert.equal(row.homePending, false)
  assert.equal(row.homeAliasTip, '利雅得希拉尔')
  assert.equal(row.away, '坎布尔')
  assert.equal(row.awayPending, true)
  assert.equal(row.awayAliasTip, '金堡尔')
  assert.equal(row.matchup, '利雅得新月 vs 坎布尔')
  assert.equal(String(row.homeAliasTip).includes('别名'), false)
  assert.equal(String(row.leagueAliasTip).includes('别名：'), false)
})

test('没有规范名时数据表回到来源文本，已升格的常用名不因编号标星', () => {
  const row = adapter.normalizeTableRow(adapter.fromBackendRow(backendRow({
    league_canonical: null,
    home_team: '利雅得希拉尔',
    away_team: '杜连斯',
    home_team_canonical: null,
    away_team_canonical: null,
  })))
  assert.equal(row.home, '利雅得希拉尔')
  assert.equal(row.homePending, false)
  assert.equal(row.away, '杜连斯')
  assert.equal(row.awayPending, true)
})

test('数据表字段直接交给与赛程相同的名称函数', () => {
  const labels = labelsForTableMatch({
    league: '巴西甲级联赛',
    league_canonical: '巴西甲',
    league_pending_confirm: false,
    home_team: '利雅得希拉尔',
    away_team: '金堡尔',
    home_team_canonical: '利雅得新月',
    away_team_canonical: '坎布尔',
    home_team_pending_confirm: false,
    away_team_pending_confirm: false,
  })
  assert.equal(labels.league, '巴西甲')
  assert.equal(labels.leaguePending, false)
  assert.equal(labels.home.text, '利雅得新月')
  assert.equal(labels.home.pending, false)
  assert.equal(labels.home.tip, '利雅得希拉尔')
  assert.equal(labels.away.text, '坎布尔')
  assert.equal(labels.away.tip, '金堡尔')
  assert.equal(labels.away.pending, false)
})
