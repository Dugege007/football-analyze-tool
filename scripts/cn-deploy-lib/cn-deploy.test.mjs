import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import {
  assertFormalDbLocalPath,
  assertHost,
  assertSafeRemotePath,
  assertTempPath,
  normalizeConfig,
  parseModes,
} from './assert-host.mjs';
import { parseEnv } from './load-env.mjs';
import { apiRemoteScript } from './mode-api.mjs';
import { dbRemoteScript } from './mode-db.mjs';
import { staticRemoteScript } from './mode-static.mjs';
import { dbHashScript, healthScript, listenScript, preflightScript } from './verify.mjs';
import {
  assertAssetCacheControl,
  assertCacheProbeText,
  assertHtmlCacheControl,
  cacheDiscoverScript,
  ensureStaticCacheInclude,
  loadCacheSnippet,
  nginxReloadScript,
  planCacheInstall,
  renderCacheApplyScript,
  splitTopLevelServers,
} from './nginx-cache.mjs';
import {
  assertUploadBytes,
  collectApiEntries,
  packTarGz,
} from './pack.mjs';
import { createRunner } from './ssh.mjs';
import {
  assertHealthJson,
  assertListenText,
  assertMatchesSample,
  tcpClosed,
} from './verify.mjs';
import { makeStamp } from '../cn-deploy.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const cli = path.join(repoRoot, 'scripts', 'cn-deploy.mjs');
const example = path.join(repoRoot, '.env.deploy.example');

function exampleConfig(overrides = {}, options = {}) {
  const raw = { ...parseEnv(fs.readFileSync(example, 'utf8')), ...overrides };
  return normalizeConfig(raw, {
    repoRoot,
    modesOverride: options.modesOverride || '',
    dryRun: options.dryRun ?? true,
    printPlan: options.printPlan ?? true,
  });
}

function runCli(args, env = process.env) {
  return spawnSync(process.execPath, [cli, ...args], {
    cwd: repoRoot,
    encoding: 'utf8',
    env,
  });
}

test('主机白名单只接受大陆站', () => {
  assert.equal(assertHost(' 124.221.56.38 '), '124.221.56.38');
  assert.throws(() => assertHost('81.70.76.42'), /拒绝发布/);
  assert.throws(() => assertHost('124.221.56.38.example'), /必须精确等于/);
  assert.throws(() => assertHost('duhongbo.com'), /禁止目标/);
  assert.throws(() => assertHost(''), /必须精确等于/);
});

test('远程路径白名单拒绝个人站、穿越和环境文件', () => {
  assert.equal(assertSafeRemotePath('/www/wwwroot/124.221.56.38'), '/www/wwwroot/124.221.56.38');
  assert.equal(assertSafeRemotePath('/opt/football-analyze-tool/.incoming'), '/opt/football-analyze-tool/.incoming');
  assert.equal(assertSafeRemotePath('/tmp/fat-web.tgz'), '/tmp/fat-web.tgz');
  assert.throws(() => assertSafeRemotePath('/www/wwwroot/124.221.56.38/../../etc'), /拒绝字符/);
  assert.throws(() => assertSafeRemotePath('/opt/football-data/env'), /env/);
  assert.throws(() => assertSafeRemotePath('/var/www/duhongbo.com'), /禁止目标|不在允许/);
  assert.throws(() => assertTempPath('/tmp/not-ours.tgz'), /fat-/);
  assert.deepEqual(parseModes('db,static,api,static'), ['static', 'api', 'db']);
  assert.throws(() => parseModes('cos'), /未知发布模式/);
});

test('正式库路径必须显式指向仓库外的 app_latest.db', () => {
  const msi = 'D:\\Du_Test\\football\\current\\app_latest.db';
  assert.equal(assertFormalDbLocalPath(msi, repoRoot), msi);
  assert.throws(() => assertFormalDbLocalPath('', repoRoot), /DEPLOY_DB_LOCAL_PATH/);
  assert.throws(() => assertFormalDbLocalPath('D:\\Du_Test\\football\\research\\app_latest.db', repoRoot), /研究副本|开发库/);
  assert.throws(() => assertFormalDbLocalPath(path.join(repoRoot, 'api', 'data', 'app_latest.db'), repoRoot), /开发库|研究副本/);
  assert.throws(() => assertFormalDbLocalPath('D:\\Du_Test\\football\\current\\app.db', repoRoot), /app_latest\.db/);
});

test('上传过滤器拒绝密钥和环境文件', () => {
  assert.throws(() => assertUploadBytes('.env', Buffer.from('A=B\n')), /拒绝上传/);
  assert.throws(() => assertUploadBytes('api/app/secret.pem', Buffer.from('nope\n')), /拒绝上传/);
  assert.throws(
    () => assertUploadBytes('api/app/x.py', Buffer.from('FIVEDOLLAR_FOOTBALL_API_KEY=abcd1234real\n')),
    /真实密钥/,
  );
  assert.doesNotThrow(() => assertUploadBytes(
    'docs/schema/note.md',
    Buffer.from('需要环境变量 FIVEDOLLAR_FOOTBALL_API_KEY（不写进文件）\n'),
  ));
  assert.doesNotThrow(() => assertUploadBytes('config/a.json', Buffer.from('API_KEY=YOUR_KEY_HERE\n')));
});

test('接口发布包含名称规范与口径文档，不含测试和私有参数', () => {
  const names = collectApiEntries(repoRoot).map((entry) => entry.name);
  assert.ok(names.includes('api/app/main.py'));
  assert.ok(names.includes('api/requirements.txt'));
  assert.ok(names.includes('api/resources/name-standard/teams.csv'));
  assert.ok(names.includes('api/resources/name-standard/competitions.csv'));
  assert.ok(names.includes('docs/schema/v1_sqlite.sql'));
  assert.ok(names.includes('config/strategy_params.example.json'));
  assert.ok(names.includes('config/open_truncation_quantiles.json'));
  assert.equal(names.some((name) => name.startsWith('api/tests/')), false);
  assert.equal(names.includes('config/strategy_params.json'), false);
  assert.equal(names.includes('.env'), false);
  assert.equal(names.includes('.env.deploy'), false);
});

test('打包结果可以被 tar 解出', () => {
  const archive = packTarGz([
    { name: 'index.html', body: Buffer.from('<script src="/assets/app.js"></script>\n') },
    { name: 'assets/app.js', body: Buffer.from('console.log(1)\n') },
  ]);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fat-tar-'));
  const file = path.join(dir, 'web.tgz');
  fs.writeFileSync(file, archive);
  const listed = spawnSync('tar', ['-tzf', file], { encoding: 'utf8' });
  assert.equal(listed.status, 0, listed.stderr);
  assert.match(listed.stdout, /index\.html/);
  assert.match(listed.stdout, /assets\/app\.js/);
  const extracted = spawnSync('tar', ['-xOf', file, 'index.html'], { encoding: 'utf8' });
  assert.match(extracted.stdout, /\/assets\/app\.js/);
  fs.rmSync(dir, { recursive: true, force: true });
});

const baotaVhost = `server
{
    listen 80;
    server_name 124.221.56.38;
    root /www/wwwroot/124.221.56.38;
    location /api/ {
        proxy_pass http://127.0.0.1:8787/;
    }
    location ~ .*\\.(js|css)?$
    {
        expires 12h;
    }
    # 注释 } 不能截断 server
    location /note/ {
        return 200 "{";
    }
}
`;

function discoverText(files) {
  const lines = files.map((file) => {
    const encoded = Buffer.from(file.body, 'utf8').toString('base64');
    return `CANDIDATE ${file.path}\nB64 ${encoded}`;
  });
  if (files.length === 0) lines.push('CANDIDATE_NONE');
  lines.push('DISCOVER_DONE');
  return lines.join('\n');
}

test('远程脚本通过 bash 语法检查', () => {
  const cfg = exampleConfig();
  const stamp = '20261018T120000Z';
  const planned = planCacheInstall(discoverText([{
    path: '/www/server/panel/vhost/nginx/124.221.56.38.conf',
    body: baotaVhost,
  }]), stamp);
  const scripts = [
    staticRemoteScript(cfg, stamp),
    apiRemoteScript(cfg, stamp),
    dbRemoteScript(cfg, stamp, 'a'.repeat(64)),
    preflightScript(),
    healthScript(cfg),
    listenScript(),
    dbHashScript(cfg),
    cacheDiscoverScript(),
    nginxReloadScript(cfg),
    renderCacheApplyScript(cfg, planned),
  ];
  for (const script of scripts) {
    const file = path.join(os.tmpdir(), `fat-bash-${Math.random().toString(16).slice(2)}.sh`);
    fs.writeFileSync(file, script);
    const checked = spawnSync('bash', ['-n', file], { encoding: 'utf8' });
    fs.rmSync(file, { force: true });
    assert.equal(checked.status, 0, checked.stderr || script.slice(0, 200));
    assert.doesNotMatch(script, /81\.70\.76\.42/);
    assert.doesNotMatch(script, /\/opt\/football-data\/env/);
  }
});

test('干跑不调用 ssh', async () => {
  let called = false;
  const runner = createRunner({
    cfg: { sshKey: 'k', sshPort: 22, sshUser: 'root', host: '124.221.56.38' },
    dryRun: true,
    log() {},
    spawnImpl() {
      called = true;
    },
  });
  await runner.upload('/tmp/local.tgz', '/tmp/fat-web.tgz', 'temp');
  await runner.ssh('echo hi');
  assert.equal(called, false);
  await assert.rejects(
    () => runner.upload('/tmp/local.tgz', '/tmp/not-fat.tgz', 'temp'),
    /临时文件/,
  );
});

test('验收解析', () => {
  assert.equal(assertHealthJson('{"ok":true,"version":"0.3.26"}').version, '0.3.26');
  assert.throws(() => assertHealthJson('{"ok":false}'), /ok/);
  assert.doesNotThrow(() => assertListenText('LISTEN 0 128 127.0.0.1:8787 0.0.0.0:*'));
  assert.throws(() => assertListenText('LISTEN 0 128 0.0.0.0:8787 0.0.0.0:*'), /非回环/);
  const sample = assertMatchesSample(JSON.stringify({
    items: [{ match: { home_team_canonical: '甲', home_team_pending_confirm: false } }],
  }));
  assert.equal(sample.sampled, true);
  assert.equal(assertMatchesSample('{"items":[]}').sampled, false);
  assert.match(makeStamp(new Date('2026-10-18T12:15:00.123Z')), /^20261018T121500Z$/);
});

test('本机未监听的端口视为未开放', async () => {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  assert.equal(await tcpClosed('127.0.0.1', port, 1000), false);
  await new Promise((resolve) => server.close(resolve));
  assert.equal(await tcpClosed('127.0.0.1', port, 1000), true);
});

test('示例配置可以干跑且不连接', () => {
  const result = runCli(['--dry-run', '--yes', '--env-file', example]);
  const output = `${result.stdout}\n${result.stderr}`;
  assert.equal(result.status, 0, output);
  assert.match(output, /目标主机：124\.221\.56\.38/);
  assert.match(output, /发布模式：static, api/);
  assert.match(output, /正式库：本次不覆盖/);
  assert.match(output, /干跑完成/);
  assert.match(output, /不连接 SSH/);
  assert.match(output, /静态缓存：入口文档 no-cache/);
  assert.match(output, /expires -1/);
  assert.match(output, /fat-static-cache\.conf/);
  assert.match(output, /不得带大于 0 的 max-age/);
  assert.doesNotMatch(output, /81\.70\.76\.42/);
});

test('持续集成不能实连，但可以干跑', () => {
  const env = { ...process.env, CI: 'true' };
  const blocked = runCli(['--yes', '--env-file', example], env);
  assert.notEqual(blocked.status, 0);
  assert.match(`${blocked.stdout}\n${blocked.stderr}`, /持续集成/);
  const dry = runCli(['--dry-run', '--yes', '--env-file', example], env);
  assert.equal(dry.status, 0, `${dry.stdout}\n${dry.stderr}`);
});

test('错误主机在干跑时也会失败', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fat-env-'));
  const file = path.join(dir, 'bad.env');
  const raw = fs.readFileSync(example, 'utf8').replace('DEPLOY_SSH_HOST=124.221.56.38', 'DEPLOY_SSH_HOST=81.70.76.42');
  fs.writeFileSync(file, raw);
  const result = runCli(['--dry-run', '--yes', '--env-file', file]);
  assert.notEqual(result.status, 0);
  assert.match(`${result.stdout}\n${result.stderr}`, /81\.70\.76\.42/);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('正式库模式的计划会写明覆盖', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fat-env-'));
  const file = path.join(dir, 'db.env');
  const db = path.join(dir, 'app_latest.db');
  fs.writeFileSync(db, 'not-a-real-database');
  const raw = `${fs.readFileSync(example, 'utf8')}\nDEPLOY_MODES=static,api,db\nDEPLOY_DB_LOCAL_PATH=${db}\n`;
  fs.writeFileSync(file, raw);
  const result = runCli(['--print-plan', '--env-file', file]);
  const output = `${result.stdout}\n${result.stderr}`;
  assert.equal(result.status, 0, output);
  assert.match(output, /将覆盖远端正式库/);
  assert.match(output, /\/opt\/football-data\/app_latest\.db/);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('入口短缓存与指纹资源长缓存', () => {
  assert.equal(assertHtmlCacheControl('no-cache'), 'no-cache');
  assert.equal(assertHtmlCacheControl('max-age=0, must-revalidate'), 'max-age=0, must-revalidate');
  assert.throws(() => assertHtmlCacheControl(''), /没有 Cache-Control/);
  assert.throws(() => assertHtmlCacheControl('max-age=43200'), /大于 0/);
  assert.throws(() => assertHtmlCacheControl('no-cache, max-age=31536000'), /大于 0/);
  assert.throws(() => assertHtmlCacheControl('public, s-maxage=3600'), /s-maxage/);
  assert.equal(
    assertAssetCacheControl('public, max-age=31536000, immutable'),
    'public, max-age=31536000, immutable',
  );
  assert.doesNotThrow(() => assertAssetCacheControl('max-age=2592000'));
  assert.throws(() => assertAssetCacheControl('max-age=43200'), /30 天/);
  assert.throws(() => assertAssetCacheControl(''), /30 天/);
  const snippet = loadCacheSnippet();
  assert.match(snippet, /expires -1/);
  assert.match(snippet, /public, max-age=31536000, immutable/);
  assert.doesNotMatch(snippet, /location\s+\/\s*\{/);
});

test('缓存片段只合并进含网站根的 server 块，并且可以重复执行', () => {
  const redirect = `server {
    listen 80;
    server_name 124.221.56.38;
    return 301 /;
}
`;
  const merged = ensureStaticCacheInclude(`${redirect}${baotaVhost}`);
  assert.equal(merged.changed, true);
  assert.equal(merged.rootBlocks, 1);
  assert.equal(merged.text.includes('proxy_pass http://127.0.0.1:8787/;'), true);
  assert.equal(merged.text.split('fat-static-cache.conf').length - 1, 1);
  const servers = splitTopLevelServers(merged.text).filter((part) => part.kind === 'server');
  assert.equal(servers.length, 2);
  assert.equal(servers[0].text.includes('fat-static-cache.conf'), false);
  assert.equal(servers[0].text.includes('return 301 /;'), true);
  assert.equal(servers[1].text.includes('return 200 "{";'), true);
  assert.match(servers[1].text, /# 注释 \} 不能截断 server/);
  const again = ensureStaticCacheInclude(merged.text);
  assert.equal(again.changed, false);
  assert.equal(again.text, merged.text);
  const included = `server {
    root /www/wwwroot/124.221.56.38;
    include /www/server/panel/vhost/nginx/extension/124.221.56.38/*.conf;
}
`;
  const kept = ensureStaticCacheInclude(included);
  assert.equal(kept.changed, false);
  assert.equal(kept.text, included);
  assert.throws(
    () => ensureStaticCacheInclude('server { root /www/wwwroot/124.221.56.38; }\n# 81.70.76.42\n'),
    /禁止目标/,
  );
});

test('站点配置探测结果可生成缓存安装计划', () => {
  const stamp = '20261018T120000Z';
  const stdout = discoverText([{
    path: '/www/server/panel/vhost/nginx/124.221.56.38.conf',
    body: baotaVhost,
  }]);
  const plan = planCacheInstall(stdout, stamp);
  assert.equal(plan.vhosts.length, 1);
  assert.equal(plan.vhosts[0].changed, true);
  assert.equal(plan.vhosts[0].temp, '/tmp/fat-nginx-vhost-0.conf');
  assert.match(plan.backupDir, /\/opt\/football-data\/backups\/nginx\/20261018T120000Z$/);
  const probe = [
    'PROBE / no-cache',
    'PROBE /index.html max-age=0, must-revalidate',
    'PROBE /assets/index-Abc.js public, max-age=31536000, immutable',
    'CACHE_OK',
  ].join('\n');
  assert.equal(assertCacheProbeText(probe).skipped, false);
  assert.throws(() => assertCacheProbeText('PROBE / max-age=43200\nPROBE /index.html no-cache\nPROBE /assets/a.js max-age=31536000\nCACHE_OK'), /大于 0/);
  assert.equal(assertCacheProbeText('PROBE_SKIP\nCACHE_OK').skipped, true);
  assert.throws(() => planCacheInstall('CANDIDATE_NONE\nDISCOVER_DONE', stamp), /没有自动合并/);
  assert.doesNotMatch(cacheDiscoverScript(), /81\.70\.76\.42/);
  assert.doesNotMatch(cacheDiscoverScript(), /duhongbo/i);
});

test('Git 忽略实配但保留示例', () => {
  const ignored = spawnSync('git', ['check-ignore', '-q', '.env.deploy'], { cwd: repoRoot });
  const ignoredLocal = spawnSync('git', ['check-ignore', '-q', '.env.deploy.local'], { cwd: repoRoot });
  const exampleIgnored = spawnSync('git', ['check-ignore', '-q', '.env.deploy.example'], { cwd: repoRoot });
  assert.equal(ignored.status, 0);
  assert.equal(ignoredLocal.status, 0);
  assert.equal(exampleIgnored.status, 1);
});
