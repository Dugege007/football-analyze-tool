import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { FIXED, assertStamp, assertBackupPath, assertTempPath, assertSafeRemotePath, DeployError } from './assert-host.mjs';
import { bashQuote } from './bash.mjs';
import { collectApiEntries, packTarGz } from './pack.mjs';

export function apiBackupPath(cfg, stamp) {
  assertStamp(stamp);
  return assertBackupPath(`${cfg.dbBackupDir}/app/${stamp}`);
}

export function apiRemoteScript(cfg, stamp) {
  const backup = apiBackupPath(cfg, stamp);
  const tarball = assertTempPath('/tmp/fat-api.tgz');
  const listFile = assertTempPath(`/tmp/fat-api-files-${stamp}`);
  const incoming = assertSafeRemotePath(`${cfg.appRoot}/.incoming`, '接口 incoming');
  return [
    'set -euo pipefail',
    'umask 022',
    `APP=${bashQuote(cfg.appRoot, '应用根')}`,
    `VENV=${bashQuote(cfg.venv, '虚拟环境')}`,
    `UNIT=${bashQuote(cfg.unit, '服务单元')}`,
    `BACKUP=${bashQuote(backup, '接口备份')}`,
    `TGZ=${bashQuote(tarball, '接口压缩包')}`,
    `LIST=${bashQuote(listFile, '文件清单')}`,
    `INCOMING=${bashQuote(incoming, '接口 incoming')}`,
    `KEEP=${cfg.backupKeep}`,
    'test -f "$TGZ"',
    'rm -rf "$INCOMING"',
    'mkdir -p "$INCOMING"',
    'tar -xzf "$TGZ" -C "$INCOMING"',
    'test -f "$INCOMING/api/app/main.py"',
    'test -f "$INCOMING/api/requirements.txt"',
    'test -f "$INCOMING/api/resources/name-standard/teams.csv"',
    'test -f "$INCOMING/api/resources/name-standard/competitions.csv"',
    'find "$INCOMING" -type f -printf "%P\\n" | sort > "$LIST"',
    'old=""',
    'if [ -f "$APP/api/requirements.txt" ]; then old=$(sha256sum "$APP/api/requirements.txt" | awk \'{print $1}\'); fi',
    'new=$(sha256sum "$INCOMING/api/requirements.txt" | awk \'{print $1}\')',
    'mkdir -p "$BACKUP"',
    'backed=0',
    'while IFS= read -r rel; do',
    '  src="$APP/$rel"',
    '  if [ -f "$src" ]; then',
    '    mkdir -p "$BACKUP/$(dirname "$rel")"',
    '    cp -a "$src" "$BACKUP/$rel"',
    '    backed=1',
    '  fi',
    'done < "$LIST"',
    'if [ "$backed" -eq 0 ]; then echo first > "$BACKUP/FIRST_DEPLOY"; fi',
    'test -d "$BACKUP"',
    'while IFS= read -r rel; do',
    '  src="$INCOMING/$rel"',
    '  dst="$APP/$rel"',
    '  mkdir -p "$(dirname "$dst")"',
    '  cp -a "$src" "$dst"',
    '  chmod 644 "$dst"',
    'done < "$LIST"',
    'if [ -n "$old" ] && [ "$old" = "$new" ]; then',
    '  echo "requirements 未变化，跳过 pip"',
    'else',
    '  PY="$VENV/bin/python"',
    '  if [ ! -x "$PY" ]; then echo "找不到 $PY，且 requirements 有变化。未重启服务。" >&2; exit 1; fi',
    '  "$PY" -c \'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)\'',
    '  if ! "$PY" -m pip install -r "$APP/api/requirements.txt"; then',
    '    echo "pip 安装失败，按方案不重启服务。代码备份：$BACKUP" >&2',
    '    exit 1',
    '  fi',
    'fi',
    'systemctl restart "$UNIT"',
    'ok=0',
    'for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do',
    '  if command -v curl >/dev/null 2>&1; then',
    '    if curl -fsS --max-time 3 http://127.0.0.1:8787/health >/tmp/fat-health.json; then ok=1; break; fi',
    '  elif "$VENV/bin/python" -c \'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=3).read()\'; then',
    '    ok=1; break',
    '  fi',
    '  sleep 1',
    'done',
    'if [ "$ok" -ne 1 ]; then',
    '  echo "服务重启后健康检查失败。代码备份：$BACKUP" >&2',
    '  exit 1',
    'fi',
    'rm -rf "$INCOMING" "$LIST" "$TGZ"',
    'parent="$(dirname "$BACKUP")"',
    'i=0',
    'for d in $(ls -1dt "$parent"/*/ 2>/dev/null || true); do',
    '  i=$((i+1))',
    '  if [ "$i" -gt "$KEEP" ]; then rm -rf "$d"; fi',
    'done',
    'echo "API_OK $BACKUP"',
    '',
  ].join('\n');
}

export function apiRollback(backup) {
  return [
    '接口代码回滚：',
    `  systemctl stop ${FIXED.unit}`,
    `  将 ${backup}/ 下的相对路径拷回 ${FIXED.appRoot}/`,
    `  systemctl start ${FIXED.unit}`,
    '  已成功的其他段不会自动回滚。不要改 systemd 单元的 User，也不要覆盖 /opt/football-data/env。',
  ].join('\n');
}

export async function runApi(ctx) {
  const { cfg, repoRoot, runner, stamp, state, log } = ctx;
  const backup = apiBackupPath(cfg, stamp);
  const entries = collectApiEntries(repoRoot);
  log(`接口：将同步 ${entries.length} 个文件（api/app、requirements、名称规范、口径文档、非密钥配置）`);
  if (cfg.printPlan) return;
  state.rollbacks.push(apiRollback(backup));
  if (cfg.dryRun) {
    log(`[干跑] 将上传 /tmp/fat-api.tgz，备份到 ${backup}，覆盖 ${cfg.appRoot} 后重启 ${cfg.unit}`);
    log('[干跑] 不修改 /opt/football-data/env，不修改 systemd User');
    await runner.ssh(apiRemoteScript(cfg, stamp), '接口：远端步骤预览');
    return;
  }
  const archive = packTarGz(entries);
  const localTar = path.join(os.tmpdir(), `fat-api-${stamp}.tgz`);
  fs.writeFileSync(localTar, archive);
  try {
    await runner.upload(localTar, '/tmp/fat-api.tgz', 'temp');
    const result = await runner.ssh(apiRemoteScript(cfg, stamp), '接口：备份、覆盖、按需安装依赖并重启');
    if (!/API_OK\s+\S+/.test(result.stdout)) {
      throw new DeployError(`接口发布没有返回备份路径。可手工回滚：\n${apiRollback(backup)}`);
    }
  } finally {
    fs.rmSync(localTar, { force: true });
  }
}
