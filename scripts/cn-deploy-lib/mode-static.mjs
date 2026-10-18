import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { FIXED, assertStamp, assertBackupPath, assertTempPath, DeployError } from './assert-host.mjs';
import { bashQuote } from './bash.mjs';
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
  const nginx = cfg.nginxCmd
    ? cfg.nginxCmd
    : [
      'if [ -x /www/server/nginx/sbin/nginx ]; then',
      '  /www/server/nginx/sbin/nginx -t',
      '  /www/server/nginx/sbin/nginx -s reload',
      'elif command -v nginx >/dev/null 2>&1; then',
      '  nginx -t',
      '  nginx -s reload',
      'else',
      '  echo "找不到 nginx，静态文件已覆盖，请按备份回滚" >&2',
      '  exit 1',
      'fi',
    ].join('\n');
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
    nginx,
    'i=0',
    'for d in $(ls -1dt "$(dirname "$BACKUP")"/*/ 2>/dev/null || true); do',
    '  i=$((i+1))',
    '  if [ "$i" -gt "$KEEP" ]; then rm -rf "$d"; fi',
    'done',
    'echo "STATIC_OK $BACKUP"',
    '',
  ].join('\n');
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
    return;
  }

  const entries = collectDistEntries(distDir);
  const archive = packTarGz(entries);
  const localTar = path.join(os.tmpdir(), `fat-web-${stamp}.tgz`);
  fs.writeFileSync(localTar, archive);
  try {
    await runner.upload(localTar, '/tmp/fat-web.tgz', 'temp');
    const result = await runner.ssh(staticRemoteScript(cfg, stamp), '静态：远端备份、覆盖并重载 Nginx');
    if (!/STATIC_OK\s+\S+/.test(result.stdout)) {
      throw new DeployError(`静态发布没有返回备份路径。可手工回滚：\n${staticRollback(backup)}`);
    }
  } finally {
    fs.rmSync(localTar, { force: true });
  }
}
