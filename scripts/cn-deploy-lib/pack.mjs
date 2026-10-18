import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { gzipSync } from 'node:zlib';
import { DeployError } from './assert-host.mjs';

const DENY_REL = [
  /(^|\/)\.env($|\.)/i,
  /(^|\/)\.git(\/|$)/,
  /(^|\/)\.venv(\/|$)/,
  /(^|\/)node_modules(\/|$)/,
  /(^|\/)__pycache__(\/|$)/,
  /\.pyc$/,
  /\.(pem|key|cookie)$/i,
  /(^|\/)id_(rsa|ed25519|ecdsa|dsa)($|\.)/i,
  /cookies/i,
  /(^|\/)secrets?(\/|$)/i,
  /^api\/tests(\/|$)/,
  /^api\/data(\/|$)/,
  /^api\/backups(\/|$)/,
  /^api\/scripts(\/|$)/,
  /^config\/strategy_params\.json$/,
  /^api\/config\/kickoff_drift\.json$/,
  /^api\/config\/leak_suspect\.json$/,
  /\.(db|sqlite|sqlite3)$/i,
];

const SECRET_ASSIGN = /([A-Z0-9_]*(?:API_KEY|SECRET|PASSWORD|PRIVATE_KEY|TOKEN))\s*[:=]\s*['"]?([^\s'"#]+)/g;

function isPlaceholder(value) {
  if (/^your_/i.test(value)) return true;
  if (/^(changeme|example|placeholder|todo|null|none|false|true|set|missing)$/i.test(value)) return true;
  if (/^(x{3,}|\*+)$/i.test(value)) return true;
  if (/^<[^>]+>$/.test(value)) return true;
  return false;
}

const CONFIG_FILES = [
  'config/strategy_params.example.json',
  'config/open_truncation_quantiles.example.json',
  'config/open_truncation_quantiles.json',
  'api/config/kickoff_drift.example.json',
  'api/config/leak_suspect.example.json',
];

const SCHEMA_EXT = new Set(['.md', '.sql', '.json', '.txt', '.csv', '.yml', '.yaml']);

export function assertArchiveName(name) {
  if (!name || typeof name !== 'string') {
    throw new DeployError('打包路径为空。');
  }
  if (name.startsWith('/') || name.startsWith('\\') || name.includes('\\') || name.includes('\0')) {
    throw new DeployError(`拒绝打包绝对路径或反斜杠：${name}`);
  }
  const parts = name.split('/');
  if (parts.some((part) => part === '' || part === '.' || part === '..')) {
    throw new DeployError(`拒绝打包不安全相对路径：${name}`);
  }
  return name;
}

export function assertUploadRel(rel) {
  const name = assertArchiveName(rel);
  for (const pattern of DENY_REL) {
    if (pattern.test(name)) {
      throw new DeployError(`拒绝上传 ${name}。一键发布不携带环境文件、密钥、测试、开发库或私有参数。`);
    }
  }
  return name;
}

export function assertUploadBytes(rel, body) {
  assertUploadRel(rel);
  if (!Buffer.isBuffer(body)) {
    throw new DeployError(`打包内容不是二进制缓冲：${rel}`);
  }
  if (body.includes(0)) {
    throw new DeployError(`拒绝上传含空字节的文件：${rel}`);
  }
  const text = body.toString('utf8');
  if (text.includes('-----BEGIN ')) {
    throw new DeployError(`拒绝上传私钥内容：${rel}`);
  }
  SECRET_ASSIGN.lastIndex = 0;
  let match = SECRET_ASSIGN.exec(text);
  while (match) {
    const value = match[2];
    if (value.length >= 8 && !isPlaceholder(value)) {
      throw new DeployError(`拒绝上传含真实密钥赋值的文件：${rel}`);
    }
    match = SECRET_ASSIGN.exec(text);
  }
  return body;
}

function walkFiles(dir, out) {
  if (!fs.existsSync(dir)) return;
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ent.isSymbolicLink()) continue;
    const abs = path.join(dir, ent.name);
    if (ent.isDirectory()) {
      walkFiles(abs, out);
    } else if (ent.isFile()) {
      out.push(abs);
    }
  }
}

function relPosix(repoRoot, abs) {
  return path.relative(repoRoot, abs).split(path.sep).join('/');
}

export function collectApiEntries(repoRoot) {
  const files = [];
  walkFiles(path.join(repoRoot, 'api', 'app'), files);
  walkFiles(path.join(repoRoot, 'api', 'resources', 'name-standard'), files);
  walkFiles(path.join(repoRoot, 'docs', 'schema'), files);
  for (const rel of CONFIG_FILES) {
    const abs = path.join(repoRoot, rel);
    if (fs.existsSync(abs) && fs.statSync(abs).isFile()) files.push(abs);
  }
  const requirements = path.join(repoRoot, 'api', 'requirements.txt');
  if (!fs.existsSync(requirements)) {
    throw new DeployError('缺少 api/requirements.txt，无法组装接口发布包。');
  }
  files.push(requirements);

  const entries = [];
  const seen = new Set();
  for (const abs of files) {
    const rel = relPosix(repoRoot, abs);
    if (seen.has(rel)) continue;
    seen.add(rel);
    if (rel.startsWith('api/app/') && !rel.endsWith('.py')) {
      throw new DeployError(`api/app 里出现了非 Python 文件，已中止：${rel}`);
    }
    if (rel.startsWith('api/resources/name-standard/') && !rel.endsWith('.csv')) {
      throw new DeployError(`名称规范资源只允许 csv：${rel}`);
    }
    if (rel.startsWith('docs/schema/')) {
      const ext = path.posix.extname(rel);
      if (!SCHEMA_EXT.has(ext)) {
        throw new DeployError(`口径文档扩展名不在允许列表内：${rel}`);
      }
    }
    const body = fs.readFileSync(abs);
    if (body.length > 5 * 1024 * 1024) {
      throw new DeployError(`接口发布包里的文件过大：${rel}`);
    }
    assertUploadBytes(rel, body);
    entries.push({ name: rel, body });
  }
  entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  const names = new Set(entries.map((entry) => entry.name));
  for (const required of [
    'api/app/main.py',
    'api/requirements.txt',
    'api/resources/name-standard/teams.csv',
    'api/resources/name-standard/competitions.csv',
    'docs/schema/v1_sqlite.sql',
  ]) {
    if (!names.has(required)) {
      throw new DeployError(`接口发布包缺少 ${required}。`);
    }
  }
  return entries;
}

export function collectDistEntries(distDir) {
  if (!fs.existsSync(path.join(distDir, 'index.html'))) {
    throw new DeployError('没有找到 web/dist/index.html。请先构建，或去掉 --skip-build。');
  }
  const files = [];
  walkFiles(distDir, files);
  const entries = [];
  for (const abs of files) {
    const rel = path.relative(distDir, abs).split(path.sep).join('/');
    if (rel.split('/').some((part) => part.startsWith('.'))) continue;
    assertUploadRel(rel);
    const body = fs.readFileSync(abs);
    if (body.includes(0)) {
      entries.push({ name: rel, body });
      continue;
    }
    assertUploadBytes(rel, body);
    if (body.length > 30 * 1024 * 1024) {
      throw new DeployError(`前端产物单文件过大：${rel}`);
    }
    entries.push({ name: rel, body });
  }
  const names = new Set(entries.map((entry) => entry.name));
  if (!names.has('index.html')) {
    throw new DeployError('前端产物缺少 index.html。');
  }
  if (![...names].some((name) => name.startsWith('assets/'))) {
    throw new DeployError('前端产物缺少 assets/。');
  }
  let total = 0;
  for (const entry of entries) total += entry.body.length;
  if (total > 80 * 1024 * 1024) {
    throw new DeployError('前端产物总体积超过 80MB，已中止。');
  }
  entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  return entries;
}

export function readAssetRefs(html) {
  const text = String(html ?? '');
  const refs = [];
  const pattern = /(?:src|href)="(\.?\/assets\/[^"]+)"/g;
  let match = pattern.exec(text);
  while (match) {
    refs.push(match[1]);
    match = pattern.exec(text);
  }
  return refs;
}

function writeOctal(header, offset, length, value) {
  const digits = value.toString(8).padStart(length - 1, '0');
  header.write(digits, offset, length - 1, 'ascii');
  header[offset + length - 1] = 0;
}

function tarHeader(name, size, mode = 0o644) {
  assertArchiveName(name);
  const header = Buffer.alloc(512, 0);
  let prefix = '';
  let base = name;
  if (Buffer.byteLength(name) > 100) {
    const slash = name.lastIndexOf('/');
    const fileLength = Buffer.byteLength(name.slice(slash + 1));
    const prefixLength = slash > 0 ? Buffer.byteLength(name.slice(0, slash)) : 999;
    if (slash <= 0 || prefixLength > 155 || fileLength > 100) {
      throw new DeployError(`路径过长，无法打包：${name}`);
    }
    prefix = name.slice(0, slash);
    base = name.slice(slash + 1);
  }
  header.write(base, 0, 'utf8');
  writeOctal(header, 100, 8, mode);
  writeOctal(header, 108, 8, 0);
  writeOctal(header, 116, 8, 0);
  writeOctal(header, 124, 12, size);
  writeOctal(header, 136, 12, Math.floor(Date.now() / 1000));
  header.fill(0x20, 148, 156);
  header.write('0', 156, 1, 'ascii');
  header.write('ustar', 257, 6, 'ascii');
  header.write('00', 263, 2, 'ascii');
  if (prefix) header.write(prefix, 345, 'utf8');
  let checksum = 0;
  for (let i = 0; i < 512; i += 1) checksum += header[i];
  const chk = checksum.toString(8).padStart(6, '0');
  header.write(chk, 148, 6, 'ascii');
  header[154] = 0;
  header[155] = 0x20;
  return header;
}

export function packTar(entries) {
  const chunks = [];
  for (const entry of entries) {
    const name = assertArchiveName(entry.name);
    const body = Buffer.isBuffer(entry.body) ? entry.body : Buffer.from(entry.body);
    if (!name.startsWith('assets/') && !name.includes('\0')) {
      if (!body.includes(0)) assertUploadBytes(name, body);
    } else if (!body.includes(0)) {
      assertUploadBytes(name, body);
    }
    chunks.push(tarHeader(name, body.length));
    chunks.push(body);
    const pad = (512 - (body.length % 512)) % 512;
    if (pad) chunks.push(Buffer.alloc(pad, 0));
  }
  chunks.push(Buffer.alloc(1024, 0));
  return Buffer.concat(chunks);
}

export function packTarGz(entries) {
  return gzipSync(packTar(entries));
}

export function hashFile(filePath) {
  return new Promise((resolve, reject) => {
    const hash = createHash('sha256');
    const input = fs.createReadStream(filePath);
    input.on('error', reject);
    input.on('data', (chunk) => hash.update(chunk));
    input.on('end', () => resolve(hash.digest('hex')));
  });
}

export function hashBuffer(body) {
  return createHash('sha256').update(body).digest('hex');
}
