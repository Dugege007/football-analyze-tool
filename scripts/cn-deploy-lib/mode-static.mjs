import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { FIXED, assertStamp, assertBackupPath, assertTempPath, DeployError } from './assert-host.mjs';
import { bashQuote } from './bash.mjs';
import {
  assertCacheProbeText,
  cacheDiscoverScript,
  cacheRollback,
  loadCacheSnippet,
  nginxReloadScript,
  planCacheInstall,
  renderCacheApplyScript,
  SNIPPET_REMOTE,
} from './nginx-cache.mjs';
import { collectDistEntries, packTarGz, readAssetRefs } from './pack.mjs';

function runInherit(cmd, args, cwd) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      cwd,
      shell: process.platform === 'win32',
      stdio: 'inherit',
    });
    child.on('error', (error) => {
      if (error && error.code === 'ENOENT') {
        reject(new DeployError(`没有找到命令 ${cmd}。`));
        return;
      }
      reject(error);
    });
    child.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new DeployError(`${cmd} ${args.join(' ')} 退出码 ${code}`));
    });
  });
}

export function staticBackupPath(cfg, stamp) {
  assertStamp(stamp);
  return assertBackupPath(`${cfg.dbBackupDir}/wwwroot/${stamp}`);
}

export function staticRemoteScript(cfg, stamp) {
  const backup = staticBackupPath(cfg, stamp);
  const stage = assertTempPath(`/tmp/fat-web-stage-${stamp}`);
  const tarball = assertTempPath('/tmp/fat-web.tgz');
  const webRoot = bashQuote(cfg.webRoot, '网站根');
  return [
    'set -euo pipefail',
    'umask 022',
    `WEB_ROOT=${webRoot}`,
    `BACKUP=${bashQuote(backup, '静态备份')}`,
    `STAGE=${bashQuote(stage, '静态临时目录')}`,
    `TGZ=${bashQuote(tarball, '静态压缩包')}`,
    `KEEP=${cfg.backupKeep}`,
    'mkdir -p "$(dirname "$BACKUP")" "$BACKUP"',
    'if [ -d "$WEB_ROOT" ] && [ -n "$(ls -A "$WEB_ROOT" 2>/dev/null || true)" ]; then',
    '  cp -a "$WEB_ROOT"/. "$BACKUP"/',
    '  if [ -z "$(ls -A "$BACKUP" 2>/dev/null || true)" ]; then',
    '    echo "静态备份失败：备份目录是空的" >&2',
    '    exit 1',
    '  fi',
    'else',
    '  echo "网站根目前为空，已记下空备份" ',
    '  echo empty > "$BACKUP/EMPTY_WEB_ROOT"',
    'fi',
    'rm -rf "$STAGE"',
    'mkdir -p "$STAGE"',
    'tar -xzf "$TGZ" -C "$STAGE"',
    'test -f "$STAGE/index.html"',
    'test -d "$STAGE/assets"',
    'mkdir -p "$WEB_ROOT"',
    'if command -v rsync >/dev/null 2>&1; then',
    '  rsync -a --delete "$STAGE"/ "$WEB_ROOT"/',
    'else',
    '  cp -a "$STAGE"/. "$WEB_ROOT"/',
    'fi',
    'find "$WEB_ROOT" -type d -exec chmod 755 {} +',
    'find "$WEB_ROOT" -type f -exec chmod 644 {} +',
    'rm -rf "$STAGE"',
    'rm -f "$TGZ"',
    'i=0',
    'for d in $(ls -1dt "$(dirname "$BACKUP")"/*/ 2>/dev/null || true); do',
    '  i=$((i+1))',
    '  if [ "$i" -gt "$KEEP" ]; then rm -rf "$d"; fi',
    'done',
    'echo "STATIC_OK $BACKUP"',
    '',
  ].join('\n');
}

async function uploadText(runner, content, remotePath, stamp, index) {
  const local = path.join(os.tmpdir(), `fat-nginx-vhost-${index}-${stamp}.conf`);
  fs.writeFileSync(local, content);
  try {
    await runner.upload(local, remotePath, 'temp');
  } finally {
    fs.rmSync(local, { force: true });
  }
}

async function reloadNginxBestEffort(ctx) {
  try {
    await ctx.runner.ssh(nginxReloadScript(ctx.cfg), '静态：重载 Nginx');
  } catch (error) {
    ctx.log(`静态：Nginx 重载失败：${error.message || error}`);
  }
}

export async function installStaticCache(ctx) {
  const { cfg, runner, stamp, state, log } = ctx;
  if (cfg.dryRun || cfg.printPlan) {
    await runner.ssh(cacheDiscoverScript(), '静态：探测站点配置（实跑才执行）');
    log(`静态：缓存片段将写入 ${SNIPPET_REMOTE}`);
    log(loadCacheSnippet().trimEnd());
    log('静态：含网站根的 server 块若还没有该 include，会先备份再插入。nginx -t 失败则还原。');
    log('静态：随后在服务器本机探测 / 、/index.html 与 /assets/ 的 Cache-Control。');
    return;
  }
  const discovered = await runner.ssh(cacheDiscoverScript(), '静态：探测站点配置', { quiet: true });
  let plan;
  try {
    plan = planCacheInstall(discovered.stdout, stamp);
  } catch (error) {
    await reloadNginxBestEffort(ctx);
    throw error;
  }
  for (const vhost of plan.vhosts) {
    if (!vhost.changed) {
      log(`静态：${vhost.path} 已包含缓存片段，只更新片段文件`);
      continue;
    }
    log(`静态：将把缓存 include 合并进 ${vhost.path}`);
    const index = plan.vhosts.indexOf(vhost);
    await uploadText(runner, vhost.body, vhost.temp, stamp, index);
  }
  state.rollbacks.push(cacheRollback(plan.backupDir));
  const applied = await runner.ssh(renderCacheApplyScript(cfg, plan), '静态：写入缓存片段并重载 Nginx');
  if (!applied.stdout.includes('CACHE_OK')) {
    throw new DeployError('缓存配置没有完成。');
  }
  const probe = assertCacheProbeText(applied.stdout);
  if (probe.skipped) log('静态：服务器本机未能完成缓存探测，将改由公网验收确认。');
  else log('静态：服务器本机已确认入口为短缓存，指纹资源为长缓存。');
}

export function staticRollback(backup) {
  return [
    '静态站点回滚：',
    `  将 ${backup}/ 的内容拷回 ${FIXED.webRoot}/`,
    '  然后执行 nginx -t 与 nginx -s reload（宝塔路径一般为 /www/server/nginx/sbin/nginx）',
    '  已成功的其他段不会自动回滚。',
  ].join('\n');
}

export async function runStatic(ctx) {
  const { cfg, repoRoot, runner, stamp, state, log } = ctx;
  const distDir = path.join(repoRoot, 'web', 'dist');
  const backup = staticBackupPath(cfg, stamp);
  if (ctx.skipBuild) {
    log('静态：跳过构建，使用已有 web/dist');
    if (!fs.existsSync(path.join(distDir, 'index.html'))) {
      throw new DeployError('指定了 --skip-build，但 web/dist/index.html 不存在。');
    }
    const tip = path.join(distDir, '.cn-deploy-tip.txt');
    if (fs.existsSync(tip)) log(`静态：可选指纹 ${fs.readFileSync(tip, 'utf8').trim()}`);
    else log('静态：未找到可选指纹文件 web/dist/.cn-deploy-tip.txt，不阻塞发布。');
  } else if (cfg.dryRun || cfg.printPlan) {
    log('静态：实跑时将在 web/ 执行 npm run build');
  } else {
    if (!fs.existsSync(path.join(repoRoot, 'web', 'node_modules'))) {
      throw new DeployError('没有找到 web/node_modules。请先在 web 目录执行 npm ci，再发布。');
    }
    log('静态：构建 web/');
    await runInherit('npm', ['run', 'build'], path.join(repoRoot, 'web'));
  }

  if (fs.existsSync(path.join(distDir, 'index.html'))) {
    const html = fs.readFileSync(path.join(distDir, 'index.html'), 'utf8');
    state.assetRefs = readAssetRefs(html);
    if (state.assetRefs.length === 0 && !cfg.dryRun && !cfg.printPlan) {
      throw new DeployError('index.html 里没有 /assets/ 引用，无法在发布后核对指纹。');
    }
    if (state.assetRefs.length > 0) log(`静态：资源指纹 ${state.assetRefs[0]}`);
  }

  if (fs.existsSync(path.join(distDir, 'index.html')) && (cfg.dryRun || cfg.printPlan)) {
    collectDistEntries(distDir);
  }
  if (cfg.printPlan) return;
  state.rollbacks.push(staticRollback(backup));
  if (cfg.dryRun) {
    log(`[干跑] 将打包 web/dist 并上传到 /tmp/fat-web.tgz，备份到 ${backup} 后覆盖 ${cfg.webRoot}`);
    await runner.ssh(staticRemoteScript(cfg, stamp), '静态：远端步骤预览');
    await installStaticCache(ctx);
    return;
  }

  const entries = collectDistEntries(distDir);
  const archive = packTarGz(entries);
  const localTar = path.join(os.tmpdir(), `fat-web-${stamp}.tgz`);
  fs.writeFileSync(localTar, archive);
  try {
    await runner.upload(localTar, '/tmp/fat-web.tgz', 'temp');
    const result = await runner.ssh(staticRemoteScript(cfg, stamp), '静态：远端备份并覆盖网站根');
    if (!/STATIC_OK\s+\S+/.test(result.stdout)) {
      throw new DeployError(`静态发布没有返回备份路径。可手工回滚：\n${staticRollback(backup)}`);
    }
  } finally {
    fs.rmSync(localTar, { force: true });
  }
  await installStaticCache(ctx);
}
