// 赛事名与队名的别名悬停：一行一个，不加前缀；没有别名时不出现空文案。
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import { compileWithTypeScript } from './compile-with-tsc.mjs'

const out = mkdtempSync(join(tmpdir(), 'alias-tip-'))
compileWithTypeScript([
  'src/names/teamName.ts',
  'src/names/leagueShort.ts',
  '--outDir', out,
  '--module', 'es2022',
  '--target', 'es2022',
  '--skipLibCheck',
  '--ignoreConfig',
])
writeFileSync(join(out, 'package.json'), '{"type":"module"}')
const teams = await import(pathToFileURL(join(out, 'teamName.js')).href)
const leagues = await import(pathToFileURL(join(out, 'leagueShort.js')).href)

test('队名别名一行一个，不加前缀', () => {
  assert.equal(teams.teamAliasTip('全北现代'), '全北汽车')
  assert.equal(teams.teamAliasTip('曼城'), '曼彻斯特城')
  assert.equal(
    teams.teamAliasTip('萨尔茨堡'),
    'FC 萨尔斯堡\n萨尔斯堡\n红牛萨尔茨堡\nRB Salzburg',
  )
  assert.equal(
    teams.teamAliasTip('圣吉罗斯'),
    '圣吉罗斯联\n圣吉卢瓦斯联合\nUnion SG',
  )
  assert.equal(teams.teamAliasTip('上海海港', null, '亚冠'), 'Shanghai Port')
  assert.equal(teams.teamAliasTip('全北汽车', '全北现代'), '全北汽车')
})

test('队名没有别名时不返回空悬停', () => {
  assert.equal(teams.teamAliasTip('鹿岛鹿角'), undefined)
  assert.equal(teams.teamAliasTip('曼彻斯特城', '曼彻斯特城'), undefined)
  assert.equal(teams.teamAliasTip(null), undefined)
  assert.equal(teams.teamAliasTip('   '), undefined)
})

test('赛事别名与队名同一套，一行一个', () => {
  assert.equal(leagues.leagueAliasTip('英超', '英超', ['英超']), '英格兰超级联赛')
  assert.equal(leagues.leagueAliasTip('英超', '英格兰超级联赛', ['英格兰超级联赛']), '英格兰超级联赛')
  assert.equal(
    leagues.leagueAliasTip('美公开杯', '美公开杯', ['美公开杯']),
    'USA US Open Cup\n公开赛杯\n美国公开赛杯\n联合足协美国公开杯',
  )
  assert.equal(
    leagues.leagueAliasTip('巴甲', '巴西甲级联赛', ['巴西甲级联赛']),
    '巴西甲\n巴西甲级联赛',
  )
})

test('赛事没有别名时仍显示来源名，不返回空前缀', () => {
  assert.equal(leagues.leagueAliasTip('未识别联赛', '未识别联赛', ['未识别联赛']), '未识别联赛')
  assert.equal(leagues.leagueAliasTip(null, null, [null]), undefined)
  assert.equal(leagues.leagueAliasTip(null, '   ', ['   ']), undefined)
  assert.equal(leagues.leagueAliasTip('地方联赛', '地方联赛', ['地方联赛', '临时联赛']), '临时联赛')
})
