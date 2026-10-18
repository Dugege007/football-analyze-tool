import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { FIXED, assertStamp, assertBackupPath, assertSha256, DeployError } from './assert-host.mjs';
import { bashQuote } from './bash.mjs';
import { hashFile } from './pack.mjs';

export function dbBackupFile(cfg, stamp) {
  assertStamp(stamp);
  return assertBackupPath(`${cfg.dbBackupDir}/app_latest.db.${stamp}`);
}

export function dbRemoteScript(cfg, stamp, sha256) {
  const digest = assertSha256(sha256);
  const backup = dbBackupFile(cfg, stamp);
  return [
    'set -euo pipefail',
    'umask 077',
    `DB=${bashQuote(cfg.dbPath, '正式库')}`,
    `INCOMING=${bashQuote(cfg.dbIncoming, '正式库 incoming')}`,
    `BACKUP=${bashQuote(backup, '正式库备份')}`,
    `UNIT=${bashQuote(cfg.unit, '服务单元')}`,
    `EXPECT=${bashQuote(digest, 'SHA256')}`,
    `KEEP=${cfg.backupKeep}`,
    'mkdir -p "$(dirname "$BACKUP")"',
    'if [ ! -f "$INCOMING" ]; then echo "缺少 incoming 文件" >&2; exit 1; fi',
    'chmod 600 "$INCOMING"',
    'if ! echo "$EXPECT  $INCOMING" | sha256sum -c -; then',
    '  rm -f "$INCOMING"',
    '  echo "正式库哈希不一致，已删除 incoming，未改动现网库" >&2',
    '  exit 1',
    'fi',
    'if ! id football >/dev/null 2>&1; then',
    '  rm -f "$INCOMING"',
    '  echo "远端没有 football 用户，已删除 incoming，未改动现网库" >&2',
    '  exit 1',
    'fi',
    'grp=$(id -gn football)',
    'chown "football:$grp" "$INCOMING"',
    'chmod 600 "$INCOMING"',
    'systemctl stop "$UNIT"',
    'RESULT="$BACKUP"',
    'if [ -f "$DB" ]; then',
    '  if ! cp -a "$DB" "$BACKUP"; then',
    '    systemctl start "$UNIT" || true',
    '    echo "正式库备份失败，已尝试拉起原服务，未替换数据库" >&2',
    '    exit 1',
    '  fi',
    '  test -s "$BACKUP"',
    '  if [ -f "${DB}-wal" ]; then cp -a "${DB}-wal" "${BACKUP}.wal"; fi',
    '  if [ -f "${DB}-shm" ]; then cp -a "${DB}-shm" "${BACKUP}.shm"; fi',
    'else',
    '  echo "远端还没有正式库，本次没有旧文件可备份"',
    '  RESULT=NONE',
    'fi',
    'rm -f "${DB}-wal" "${DB}-shm"',
    'mv -f "$INCOMING" "$DB"',
    'chown "football:$grp" "$DB"',
    'chmod 600 "$DB"',
    'sync || true',
    'if ! echo "$EXPECT  $DB" | sha256sum -c -; then',
    '  echo "替换后哈希不一致。不要继续使用这份库。备份：$BACKUP" >&2',
    '  exit 1',
    'fi',
    'systemctl start "$UNIT"',
    'i=0',
    'for f in $(ls -1t "$(dirname "$BACKUP")"/app_latest.db.* 2>/dev/null || true); do',
    '  case "$f" in',
    '    *.wal|*.shm|*.missing) continue ;;',
    '  esac',
    '  i=$((i+1))',
    '  if [ "$i" -gt "$KEEP" ]; then rm -f "$f" "${f}.wal" "${f}.shm" "${f}.missing"; fi',
    'done',
    'echo "DB_OK $RESULT"',
    '',
  ].join('\n');
}

export function dbRollback(backup) {
  return [
    '正式库回滚：',
    `  systemctl stop ${FIXED.unit}`,
    `  cp -a ${backup} ${FIXED.dbIncoming}`,
    `  mv -f ${FIXED.dbIncoming} ${FIXED.dbPath}`,
    `  chown football:$(id -gn football) ${FIXED.dbPath} && chmod 600 ${FIXED.dbPath}`,
    '  如有同名 .wal / .shm 备份，不要把旧 wal 留在新库旁边；回滚时删掉现网 wal/shm 后再启动',
    `  systemctl start ${FIXED.unit}`,
    '  已成功的其他段不会自动回滚。',
  ].join('\n');
}

export async function runDb(ctx) {
  const { cfg, runner, stamp, state, log } = ctx;
  const backup = dbBackupFile(cfg, stamp);
  log('正式库：将覆盖远端正式库');
  log(`正式库：本机 ${cfg.dbLocalPath}`);
  log(`正式库：远端 ${cfg.dbPath}`);
  if (!cfg.dbLocalPath) {
    throw new DeployError('正式库模式缺少 DEPLOY_DB_LOCAL_PATH。');
  }
  const exists = fs.existsSync(cfg.dbLocalPath) && fs.statSync(cfg.dbLocalPath).isFile();
  if (!exists) {
    if (cfg.dryRun || cfg.printPlan) {
      log('正式库：本机文件目前不存在。实跑会失败。');
      log(`正式库：实跑时将上传到 ${cfg.dbIncoming}，校验 SHA256 后备份到 ${backup} 并原子替换`);
      return;
    }
    throw new DeployError(`本机正式库不存在：${cfg.dbLocalPath}`);
  }
  const digest = await hashFile(cfg.dbLocalPath);
  state.dbHash = digest;
  log(`正式库：本机 SHA256 ${digest}`);
  if (cfg.printPlan) return;
  state.rollbacks.push(dbRollback(backup));
  if (cfg.dryRun) {
    log(`[干跑] 将上传到 ${cfg.dbIncoming}，校验哈希后停服务、备份到 ${backup}、原子替换并再启动`);
    await runner.ssh(dbRemoteScript(cfg, stamp, digest), '正式库：远端步骤预览');
    return;
  }
  const copyPath = path.join(os.tmpdir(), `fat-db-${stamp}.db`);
  fs.copyFileSync(cfg.dbLocalPath, copyPath);
  try {
    fs.chmodSync(copyPath, 0o400);
  } catch {
    // Windows 上只读属性可能设置失败，哈希仍以副本为准。
  }
  try {
    const copyHash = await hashFile(copyPath);
    if (copyHash !== digest) {
      throw new DeployError('复制正式库后哈希变化，已中止，未上传。');
    }
    await runner.upload(copyPath, cfg.dbIncoming, 'db-incoming');
    const result = await runner.ssh(dbRemoteScript(cfg, stamp, digest), '正式库：校验、备份、原子替换并重启');
    if (!/DB_OK\s+\S+/.test(result.stdout)) {
      throw new DeployError(`正式库发布没有返回备份路径。可手工回滚：\n${dbRollback(backup)}`);
    }
  } finally {
    fs.rmSync(copyPath, { force: true });
  }
}
