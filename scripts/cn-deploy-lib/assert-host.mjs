/**
 * 大陆站发布的硬规则。调用方必须走这些断言，不能只在注释里约定。
 * 允许的主机、目录和服务单元都是固定值，避免把个人站或别的目录写进去。
 */
import path from 'node:path';

export class DeployError extends Error {
  constructor(message) {
    super(message);
    this.name = 'DeployError';
  }
}

export const ALLOWED_HOST = '124.221.56.38';
export const FORBIDDEN_HOST = '81.70.76.42';
export const BACKUP_KEEP = 5;

export const FIXED = Object.freeze({
  host: ALLOWED_HOST,
  sshUser: 'root',
  webRoot: '/www/wwwroot/124.221.56.38',
  appRoot: '/opt/football-analyze-tool',
  venv: '/opt/football-analyze-tool/.venv',
  unit: 'football-analyze-api',
  dbPath: '/opt/football-data/app_latest.db',
  dbIncoming: '/opt/football-data/app_latest.db.incoming',
  dbBackupDir: '/opt/football-data/backups',
  listenHost: '127.0.0.1',
  listenPort: 8787,
  backupKeep: BACKUP_KEEP,
});

const ALLOWED_PREFIXES = Object.freeze([
  FIXED.webRoot,
  FIXED.appRoot,
  '/opt/football-data',
  '/tmp',
]);

const SAFE_REMOTE = /^\/(?:[A-Za-z0-9._-]+\/)*[A-Za-z0-9._-]+$/;

const BANNED_SNIPPETS = ['81.70.76.42', 'duhongbo', 'personalsite'];

const MODE_ORDER = ['static', 'api', 'db'];

export function assertNoBannedText(value, label) {
  const text = String(value ?? '');
  const folded = text.toLowerCase();
  for (const snippet of BANNED_SNIPPETS) {
    if (folded.includes(snippet)) {
      throw new DeployError(`${label}含有禁止目标「${snippet}」。本脚本只允许大陆站 ${ALLOWED_HOST}。`);
    }
  }
  return text;
}

export function assertHost(raw) {
  const host = String(raw ?? '').trim();
  if (host === FORBIDDEN_HOST) {
    throw new DeployError(`拒绝发布：目标主机是个人站 ${FORBIDDEN_HOST}。本脚本只允许 ${ALLOWED_HOST}。`);
  }
  assertNoBannedText(host, 'DEPLOY_SSH_HOST');
  if (host !== ALLOWED_HOST) {
    throw new DeployError(`拒绝发布：DEPLOY_SSH_HOST 去掉空白后必须精确等于 ${ALLOWED_HOST}，当前为「${host || '空'}」。`);
  }
  return host;
}

export function underPrefix(remotePath, prefix) {
  return remotePath === prefix || remotePath.startsWith(`${prefix}/`);
}

export function assertSafeRemotePath(remotePath, purpose = '路径') {
  const text = assertNoBannedText(String(remotePath ?? '').trim(), purpose);
  if (!text.startsWith('/')) {
    throw new DeployError(`${purpose}必须是绝对路径：${text || '空'}`);
  }
  if (text.includes('..') || text.includes('\\') || text.includes('\0') || text.includes('//')) {
    throw new DeployError(`${purpose}含有拒绝字符：${text}`);
  }
  if (!SAFE_REMOTE.test(text)) {
    throw new DeployError(`${purpose}含有允许字符集之外的内容：${text}`);
  }
  const allowed = ALLOWED_PREFIXES.some((prefix) => underPrefix(text, prefix));
  if (!allowed) {
    throw new DeployError(`${purpose}不在允许的远程路径内：${text}`);
  }
  if (text === '/opt/football-data/env' || text.startsWith('/opt/football-data/env/') || text.startsWith('/opt/football-data/env.')) {
    throw new DeployError('拒绝改写 /opt/football-data/env。一键发布不上传、不覆盖该环境文件。');
  }
  if (text === '/tmp' || text === '/opt/football-data') {
    throw new DeployError(`${purpose}不能是过宽的目录前缀：${text}`);
  }
  return text;
}

export function assertTempPath(remotePath) {
  const text = assertSafeRemotePath(remotePath, '临时路径');
  if (!text.startsWith('/tmp/fat-')) {
    throw new DeployError(`临时文件必须以 /tmp/fat- 开头：${text}`);
  }
  return text;
}

export function assertBackupPath(remotePath) {
  const text = assertSafeRemotePath(remotePath, '备份路径');
  if (text === FIXED.dbBackupDir || !underPrefix(text, FIXED.dbBackupDir)) {
    throw new DeployError(`备份必须位于 ${FIXED.dbBackupDir}/ 之下：${text}`);
  }
  if (underPrefix(text, FIXED.webRoot)) {
    throw new DeployError(`备份不能放进网站根：${text}`);
  }
  return text;
}

export function assertFixedPath(actual, expected, label) {
  const text = assertSafeRemotePath(actual, label);
  if (text !== expected) {
    throw new DeployError(`${label}必须精确等于 ${expected}，当前为 ${text}。`);
  }
  return text;
}

export function parseModes(raw) {
  const text = String(raw ?? '').trim();
  const source = text || 'static,api';
  assertNoBannedText(source, 'DEPLOY_MODES');
  const parts = source.split(',').map((item) => item.trim()).filter(Boolean);
  if (parts.length === 0) {
    throw new DeployError('发布模式为空。可选 static、api、db。');
  }
  const known = new Set(MODE_ORDER);
  for (const part of parts) {
    if (!known.has(part)) {
      throw new DeployError(`未知发布模式「${part}」。可选 static、api、db。`);
    }
  }
  return MODE_ORDER.filter((mode) => parts.includes(mode));
}

export function assertStamp(stamp) {
  const text = String(stamp ?? '');
  if (!/^\d{8}T\d{6}Z$/.test(text)) {
    throw new DeployError(`时间戳格式不正确：${text}`);
  }
  return text;
}

export function assertSha256(hex) {
  const text = String(hex ?? '').trim().toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(text)) {
    throw new DeployError('SHA256 必须是 64 位十六进制。');
  }
  return text;
}

export function assertNginxCommand(raw) {
  const text = String(raw ?? '').trim();
  if (!text) return '';
  assertNoBannedText(text, 'DEPLOY_NGINX_CMD');
  const pattern = /^\/[A-Za-z0-9._/-]+ -t && \/[A-Za-z0-9._/-]+ -s reload$/;
  if (!pattern.test(text)) {
    throw new DeployError('DEPLOY_NGINX_CMD 只允许「绝对路径 nginx -t && 绝对路径 nginx -s reload」。留空则自动探测。');
  }
  return text;
}

export function assertUnit(raw) {
  const text = String(raw ?? '').trim();
  if (text !== FIXED.unit) {
    throw new DeployError(`DEPLOY_SYSTEMD_UNIT 必须精确等于 ${FIXED.unit}，当前为「${text || '空'}」。脚本不会改单元里的 User。`);
  }
  return text;
}

export function assertSshUser(raw) {
  const text = String(raw ?? '').trim();
  if (!/^[a-z_][a-z0-9_-]{0,31}$/i.test(text)) {
    throw new DeployError(`DEPLOY_SSH_USER 不合法：${text || '空'}`);
  }
  if (text !== FIXED.sshUser) {
    throw new DeployError(`DEPLOY_SSH_USER 必须是 ${FIXED.sshUser}。`);
  }
  return text;
}

export function assertPort(raw) {
  const text = String(raw ?? '').trim() || '22';
  if (!/^\d+$/.test(text)) {
    throw new DeployError(`DEPLOY_SSH_PORT 必须是数字：${text}`);
  }
  const port = Number(text);
  if (port !== 22) {
    throw new DeployError('DEPLOY_SSH_PORT 必须是 22。');
  }
  return port;
}

function isAbsolutePath(filePath) {
  return path.win32.isAbsolute(filePath) || path.posix.isAbsolute(filePath);
}

function insideRepo(filePath, repoRoot) {
  if (path.win32.isAbsolute(filePath) && !path.posix.isAbsolute(filePath)) {
    const abs = path.win32.normalize(filePath).toLowerCase();
    const root = path.win32.normalize(repoRoot).toLowerCase();
    return abs === root || abs.startsWith(`${root}\\`);
  }
  if (path.posix.isAbsolute(filePath)) {
    const abs = path.posix.normalize(filePath);
    const root = path.posix.normalize(repoRoot);
    return abs === root || abs.startsWith(`${root}/`);
  }
  return false;
}

export function assertFormalDbLocalPath(filePath, repoRoot) {
  const text = String(filePath ?? '').trim();
  if (!text) {
    throw new DeployError('正式库模式需要在 .env.deploy 里填写 DEPLOY_DB_LOCAL_PATH。脚本不会默认使用开发库或研究副本。');
  }
  assertNoBannedText(text, 'DEPLOY_DB_LOCAL_PATH');
  if (!isAbsolutePath(text)) {
    throw new DeployError('DEPLOY_DB_LOCAL_PATH 必须是本机绝对路径。');
  }
  const base = text.replace(/\\/g, '/').split('/').pop();
  if (base !== 'app_latest.db') {
    throw new DeployError('正式库文件名必须是 app_latest.db。');
  }
  const folded = text.replace(/\\/g, '/').toLowerCase();
  const markers = ['/research/', '/v2d3/', '/api/data/', '/api/backups/'];
  if (markers.some((marker) => folded.includes(marker)) || insideRepo(text, repoRoot)) {
    throw new DeployError('拒绝把仓库内的开发库或研究副本当作正式库上传。请填写仓库外的 app_latest.db 绝对路径。');
  }
  return text;
}

export function assertNotCi(dryRun) {
  if (dryRun) return;
  const keys = ['CI', 'GITHUB_ACTIONS', 'GITLAB_CI', 'BUILD_BUILDID', 'TF_BUILD'];
  for (const key of keys) {
    const value = process.env[key];
    if (value && value !== '0' && value !== 'false') {
      throw new DeployError(`检测到持续集成环境变量 ${key}，拒绝连接服务器。持续集成只允许 --dry-run。`);
    }
  }
}

export function normalizeConfig(raw, { repoRoot, modesOverride, dryRun, printPlan }) {
  const host = assertHost(raw.DEPLOY_SSH_HOST);
  const sshUser = assertSshUser(raw.DEPLOY_SSH_USER || FIXED.sshUser);
  const sshPort = assertPort(raw.DEPLOY_SSH_PORT || '22');
  const webRoot = assertFixedPath(raw.DEPLOY_REMOTE_WEB_ROOT, FIXED.webRoot, 'DEPLOY_REMOTE_WEB_ROOT');
  const appRoot = assertFixedPath(raw.DEPLOY_REMOTE_APP_ROOT, FIXED.appRoot, 'DEPLOY_REMOTE_APP_ROOT');
  const venv = assertFixedPath(raw.DEPLOY_REMOTE_VENV, FIXED.venv, 'DEPLOY_REMOTE_VENV');
  const unit = assertUnit(raw.DEPLOY_SYSTEMD_UNIT || FIXED.unit);
  const dbPath = assertFixedPath(raw.DEPLOY_REMOTE_DB_PATH, FIXED.dbPath, 'DEPLOY_REMOTE_DB_PATH');
  const dbBackupDir = assertSafeRemotePath(raw.DEPLOY_REMOTE_DB_BACKUP_DIR, 'DEPLOY_REMOTE_DB_BACKUP_DIR');
  if (dbBackupDir !== FIXED.dbBackupDir) {
    throw new DeployError(`DEPLOY_REMOTE_DB_BACKUP_DIR 必须精确等于 ${FIXED.dbBackupDir}。`);
  }
  const nginxCmd = assertNginxCommand(raw.DEPLOY_NGINX_CMD || '');
  const modes = parseModes(modesOverride || raw.DEPLOY_MODES);
  const sshKey = String(raw.DEPLOY_SSH_KEY ?? '').trim();
  assertNoBannedText(sshKey, 'DEPLOY_SSH_KEY');
  let dbLocalPath = String(raw.DEPLOY_DB_LOCAL_PATH ?? '').trim();
  if (modes.includes('db')) {
    dbLocalPath = assertFormalDbLocalPath(dbLocalPath, repoRoot);
  } else if (dbLocalPath) {
    dbLocalPath = assertFormalDbLocalPath(dbLocalPath, repoRoot);
  }
  return {
    host,
    sshUser,
    sshPort,
    sshKey,
    webRoot,
    appRoot,
    venv,
    unit,
    dbPath,
    dbIncoming: FIXED.dbIncoming,
    dbBackupDir,
    nginxCmd,
    modes,
    dbLocalPath,
    dryRun: Boolean(dryRun),
    printPlan: Boolean(printPlan),
    backupKeep: BACKUP_KEEP,
    listen: `${FIXED.listenHost}:${FIXED.listenPort}`,
  };
}
