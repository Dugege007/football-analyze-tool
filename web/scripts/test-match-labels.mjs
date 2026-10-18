// 赛程页名称：优先显示 GET /matches 的规范名，星号以接口布尔为准。
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import { compileWithTypeScript } from './compile-with-tsc.mjs'

const out = mkdtempSync(join(tmpdir(), 'match-labels-'))
compileWithTypeScript([
  'src/names/matchLabels.ts',
  '--outDir', out,
  '--rootDir', 'src',
  '--module', 'es2022',
  '--target', 'es2022',
  '--moduleResolution', 'bundler',
  '--skipLibCheck',
  '--ignoreConfig',
])
writeFileSync(join(out, 'package.json'), '{"type":"module"}')
const { matchLabels } = await import(pathToFileURL(join(out, 'names/matchLabels.js')).href)

function match(partial) {
  return {
    date: '2026-10-18',
    scope: 'jingcai',
    jc: null,
    teams: { home: '', away: '' },
    ...partial,
    teams: { home: '', away: '', ...(partial.teams ?? {}) },
    competition: { ...(partial.competition ?? {}) },
  }
}

test('规范名覆盖来源文本，不采用客户端旧常用名', () => {
  const labels = matchLabels(match({
    competition: { name: '瑞超' },
    league_canonical: '瑞典超',
    teams: { home: 'PEC兹沃勒', away: '利兹' },
    home_team_canonical: '兹沃勒',
    away_team_canonical: '利兹联',
    league_pending_confirm: false,
    home_team_pending_confirm: false,
    away_team_pending_confirm: true,
  }))
  assert.equal(labels.league, '瑞典超')
  assert.equal(labels.leagueTitle, '瑞超')
  assert.equal(labels.leaguePending, false)
  assert.equal(labels.home.text, '兹沃勒')
  assert.equal(labels.home.pending, false)
  assert.equal(labels.away.text, '利兹联')
  assert.equal(labels.away.pending, true)
  assert.match(labels.home.tip, /PEC兹沃勒/)
  assert.match(labels.away.tip, /利兹/)
})

test('库普斯和阿拉维斯显示接口规范名，而不是旧表或来源队名', () => {
  const labels = matchLabels(match({
    competition: { name: '芬超', canonical: '不会被用到' },
    league_canonical: '芬超',
    teams: { home: 'KuPS', away: '阿拉维斯队', home_canonical: '古比斯', away_canonical: '阿拉维斯队' },
    home_team_canonical: '库普斯',
    away_team_canonical: '阿拉维斯',
    teams_pending_confirm: { home: true, away: true },
    home_team_pending_confirm: false,
    away_team_pending_confirm: false,
  }))
  assert.equal(labels.league, '芬超')
  assert.equal(labels.home.text, '库普斯')
  assert.equal(labels.away.text, '阿拉维斯')
  assert.equal(labels.home.pending, false)
  assert.equal(labels.away.pending, false)
  assert.match(labels.home.tip, /KuPS/)
})

test('扁平字段缺席时，嵌套规范名和待确认与扁平同值', () => {
  const labels = matchLabels(match({
    competition: { name: '瑞超', canonical: '瑞典超', league_pending_confirm: true },
    teams: {
      home: 'PEC兹沃勒',
      away: '阿拉维斯队',
      home_canonical: '兹沃勒',
      away_canonical: '阿拉维斯',
    },
    teams_pending_confirm: { home: false, away: true },
  }))
  assert.equal(labels.league, '瑞典超')
  assert.equal(labels.leaguePending, true)
  assert.equal(labels.home.text, '兹沃勒')
  assert.equal(labels.home.pending, false)
  assert.equal(labels.away.text, '阿拉维斯')
  assert.equal(labels.away.pending, true)
})

test('规范名字段缺席时仍按数据表简称显示来源文本，编号不能单独取消星号', () => {
  const labels = matchLabels(match({
    competition: { name: '英格兰超级联赛' },
    league_id: 12,
    teams: { home: 'KuPS', away: '杜连斯' },
  }))
  assert.equal(labels.league, '英超')
  assert.equal(labels.leaguePending, false)
  assert.equal(labels.home.text, '古比斯')
  assert.equal(labels.home.pending, false)
  assert.equal(labels.away.text, '杜连斯')
  assert.equal(labels.away.pending, true)

  const unknown = matchLabels(match({
    competition: { name: '地方联赛' },
    league_canonical: null,
    league_id: 3,
    teams: { home: '甲队', away: '乙队' },
  }))
  assert.equal(unknown.league, '地方联赛')
  assert.equal(unknown.leaguePending, true)
})
