/**
 * 大陆站静态缓存：入口文档短缓存，带哈希的 /assets/ 长缓存。
 * 只合并 124.221.56.38 的站点配置，不改个人站。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ALLOWED_HOST, FIXED, DeployError, assertBackupPath, assertNoBannedText, assertStamp } from './assert-host.mjs';
import { bashQuote } from './bash.mjs';

const SNIPPET_END = 'FAT_CACHE_SNIPPET_END';

export const ASSET_MIN_MAX_AGE = 30 * 24 * 60 * 60;

export const SNIPPET_FILE = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../deploy/nginx/cn-static-cache.conf',
);

export const SNIPPET_REMOTE = `/www/server/panel/vhost/nginx/extension/${ALLOWED_HOST}/fat-static-cache.conf`;

export const CACHE_INCLUDE_BLOCK = [
  '    # FAT_STATIC_CACHE_BEGIN',
  `    include ${SNIPPET_REMOTE};`,
  '    # FAT_STATIC_CACHE_END',
].join('\n');

const VHOST_DIRS = Object.freeze([
  '/www/server/panel/vhost/nginx',
  '/etc/nginx/conf.d',
  '/etc/nginx/sites-enabled',
  '/etc/nginx/sites-available',
  '/usr/local/nginx/conf/vhost',
]);

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function loadCacheSnippet() {
  const text = fs.readFileSync(SNIPPET_FILE, 'utf8').replace(/\r\n/g, '\n');
  if (!text.includes('location = /index.html') || !text.includes('location = /') || !text.includes('location ^~ /assets/')) {
    throw new DeployError('缓存片段缺少入口或 /assets/ 规则。');
  }
  if (!text.includes('expires -1') || !text.includes('public, max-age=31536000, immutable')) {
    throw new DeployError('缓存片段的指令与入口短缓存、指纹资源长缓存不一致。');
  }
  if (text.includes(SNIPPET_END)) {
    throw new DeployError('缓存片段含有保留的结束标记。');
  }
  if (/location\s+\/\s*\{/.test(text)) {
    throw new DeployError('缓存片段不得使用匹配整站的 location /。');
  }
  assertNoBannedText(text, '缓存片段');
  return text.endsWith('\n') ? text : `${text}\n`;
}

export function manualCacheInstructions() {
  return [
    '没有自动合并站点缓存配置。请把 deploy/nginx/cn-static-cache.conf 复制为',
    SNIPPET_REMOTE,
    '并在大陆站含有网站根的 server 块内加入这一行（不要写入其他站点）：',
    `include ${SNIPPET_REMOTE};`,
    '然后执行 nginx -t 与 nginx -s reload。宝塔上的 nginx 一般是 /www/server/nginx/sbin/nginx。',
    '入口 / 与 /index.html 应为 no-cache 或 max-age=0；/assets/ 至少缓存 30 天。',
  ].join('\n');
}

export function assertHtmlCacheControl(cacheControl, label = '入口文档') {
  const text = String(cacheControl ?? '').trim();
  if (!text) {
    throw new DeployError(`${label}没有 Cache-Control。浏览器会按启发式缓存，重新打开页面仍可能看到旧入口。`);
  }
  const lower = text.toLowerCase();
  const ages = [...lower.matchAll(/(?:^|,\s*|;\s*)max-age\s*=\s*(\d+)/g)].map((item) => Number(item[1]));
  if (ages.some((age) => age > 0)) {
    throw new DeployError(`${label}的 Cache-Control 含有大于 0 的 max-age（${text}）。入口只能是 no-cache 或 max-age=0。`);
  }
  const shared = [...lower.matchAll(/s-maxage\s*=\s*(\d+)/g)].map((item) => Number(item[1]));
  if (shared.some((age) => age > 0)) {
    throw new DeployError(`${label}的 s-maxage 大于 0（${text}）。入口不能被共享缓存长时间保存。`);
  }
  if (!lower.includes('no-cache') && !ages.includes(0)) {
    throw new DeployError(`${label}的 Cache-Control 不是 no-cache 或 max-age=0：${text}`);
  }
  return text;
}

export function assertAssetCacheControl(cacheControl, label = '指纹资源') {
  const text = String(cacheControl ?? '').trim();
  const match = text.toLowerCase().match(/max-age\s*=\s*(\d+)/);
  const age = match ? Number(match[1]) : null;
  if (age === null || age < ASSET_MIN_MAX_AGE) {
    throw new DeployError(`${label}需要至少 30 天缓存，当前为「${text || '无 Cache-Control'}」。`);
  }
  return text;
}

export function normalizeAssetPath(ref) {
  const text = String(ref ?? '').trim();
  const normalized = text.startsWith('./assets/') ? text.slice(1) : text;
  if (!/^\/assets\/[A-Za-z0-9._-]+$/.test(normalized)) {
    throw new DeployError(`资源路径无法用于缓存探测：${text || '空'}`);
  }
  return normalized;
}

export function assertVhostPath(filePath) {
  const text = assertNoBannedText(String(filePath ?? '').trim(), '站点配置路径');
  if (!text.startsWith('/') || text.includes('..') || text.includes('\\') || text.includes('//') || text.includes('\0')) {
    throw new DeployError(`站点配置路径不合法：${text || '空'}`);
  }
  const slash = text.lastIndexOf('/');
  const parent = text.slice(0, slash);
  const base = text.slice(slash + 1);
  if (!VHOST_DIRS.includes(parent)) {
    throw new DeployError(`站点配置不在允许的目录内：${text}`);
  }
  if (!/^[A-Za-z0-9._-]+\.conf$/.test(base) || base === 'fat-static-cache.conf') {
    throw new DeployError(`站点配置文件名不合法：${base || '空'}`);
  }
  return text;
}

function skipSpaceAndComments(text, index) {
  let i = index;
  while (i < text.length) {
    const ch = text[i];
    if (ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r') {
      i += 1;
      continue;
    }
    if (ch === '#') {
      while (i < text.length && text[i] !== '\n') i += 1;
      continue;
    }
    break;
  }
  return i;
}

function isServerKeyword(text, index) {
  if (text.slice(index, index + 6) !== 'server') return false;
  const prev = index === 0 ? '' : text[index - 1];
  if (prev && /[A-Za-z0-9_]/.test(prev)) return false;
  const next = text[index + 6] || '';
  if (/[A-Za-z0-9_]/.test(next)) return false;
  return true;
}

function findMatchingBrace(text, openIndex) {
  let depth = 0;
  let i = openIndex;
  let quote = '';
  while (i < text.length) {
    const ch = text[i];
    if (quote) {
      if (ch === '\\') {
        i += 2;
        continue;
      }
      if (ch === quote) quote = '';
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      i += 1;
      continue;
    }
    if (ch === '#') {
      while (i < text.length && text[i] !== '\n') i += 1;
      continue;
    }
    if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) return i + 1;
    }
    i += 1;
  }
  throw new DeployError('站点配置的 server 块花括号没有闭合，拒绝修改。', 'UNBALANCED_SERVER');
}

export function splitTopLevelServers(text) {
  const source = String(text ?? '');
  const parts = [];
  let i = 0;
  let chunkStart = 0;
  let depth = 0;
  let quote = '';
  while (i < source.length) {
    const ch = source[i];
    if (quote) {
      if (ch === '\\') {
        i += 2;
        continue;
      }
      if (ch === quote) quote = '';
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      i += 1;
      continue;
    }
    if (ch === '#') {
      while (i < source.length && source[i] !== '\n') i += 1;
      continue;
    }
    if (ch === '{') {
      depth += 1;
      i += 1;
      continue;
    }
    if (ch === '}') {
      depth = Math.max(0, depth - 1);
      i += 1;
      continue;
    }
    if (depth === 0 && isServerKeyword(source, i)) {
      const after = skipSpaceAndComments(source, i + 'server'.length);
      if (source[after] === '{') {
        if (i > chunkStart) parts.push({ kind: 'text', text: source.slice(chunkStart, i) });
        const end = findMatchingBrace(source, after);
        parts.push({ kind: 'server', text: source.slice(i, end) });
        i = end;
        chunkStart = end;
        depth = 0;
        continue;
      }
    }
    i += 1;
  }
  if (chunkStart < source.length || parts.length === 0) {
    parts.push({ kind: 'text', text: source.slice(chunkStart) });
  }
  return parts;
}

export function stripNginxComments(text) {
  let out = '';
  let i = 0;
  let quote = '';
  const source = String(text ?? '');
  while (i < source.length) {
    const ch = source[i];
    if (quote) {
      out += ch;
      if (ch === '\\' && i + 1 < source.length) {
        out += source[i + 1];
        i += 2;
        continue;
      }
      if (ch === quote) quote = '';
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      out += ch;
      i += 1;
      continue;
    }
    if (ch === '#') {
      while (i < source.length && source[i] !== '\n') i += 1;
      continue;
    }
    out += ch;
    i += 1;
  }
  return out;
}

function serverDeclaresWebRoot(block) {
  const pattern = new RegExp(`(^|[^A-Za-z0-9_./-])root\\s+${escapeRegExp(FIXED.webRoot)}\\s*;`);
  return pattern.test(stripNginxComments(block));
}

function serverAlreadyIncludesCache(block) {
  const stripped = stripNginxComments(block);
  return stripped.includes('fat-static-cache.conf')
    || stripped.includes(`extension/${ALLOWED_HOST}/*.conf`);
}

function insertBeforeServerClose(block) {
  if (!block.endsWith('}')) {
    throw new DeployError('server 块不是以花括号结束，拒绝插入。', 'UNBALANCED_SERVER');
  }
  const head = block.slice(0, -1).replace(/[ \t]*$/, '');
  const lead = head.endsWith('\n') ? '' : '\n';
  return `${head}${lead}${CACHE_INCLUDE_BLOCK}\n}`;
}

export function ensureStaticCacheInclude(vhostText) {
  const text = String(vhostText ?? '');
  assertNoBannedText(text, '站点配置');
  if (!text.includes(FIXED.webRoot)) {
    throw new DeployError('站点配置里没有大陆站网站根，拒绝修改。', 'NO_ROOT_BLOCK');
  }
  const parts = splitTopLevelServers(text);
  let rootBlocks = 0;
  let changed = false;
  const next = parts.map((part) => {
    if (part.kind !== 'server' || !serverDeclaresWebRoot(part.text)) return part.text;
    rootBlocks += 1;
    if (serverAlreadyIncludesCache(part.text)) return part.text;
    changed = true;
    return insertBeforeServerClose(part.text);
  }).join('');
  if (rootBlocks === 0) {
    throw new DeployError('站点配置里没有包含网站根的 server 块，拒绝猜测插入位置。', 'NO_ROOT_BLOCK');
  }
  return { text: next, changed, rootBlocks };
}

export function cacheBackupDir(stamp) {
  const checked = assertStamp(stamp);
  return assertBackupPath(`${FIXED.dbBackupDir}/nginx/${checked}`);
}

export function parseDiscoverOutput(stdout) {
  const lines = String(stdout ?? '').replace(/\r\n/g, '\n').split('\n').map((line) => line.trim()).filter(Boolean);
  if (!lines.includes('DISCOVER_DONE')) {
    throw new DeployError('站点配置探测没有完成。');
  }
  const found = [];
  const seen = new Map();
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (line === 'DISCOVER_DONE' || line === 'CANDIDATE_NONE') continue;
    if (!line.startsWith('CANDIDATE ')) {
      throw new DeployError('站点配置探测出现无法识别的内容。');
    }
    const filePath = assertVhostPath(line.slice('CANDIDATE '.length).trim());
    const encoded = lines[i + 1] || '';
    if (!encoded.startsWith('B64 ')) {
      throw new DeployError(`站点配置 ${filePath} 缺少正文。`);
    }
    const body = Buffer.from(encoded.slice(4).trim(), 'base64').toString('utf8');
    if (!body.trim()) throw new DeployError(`站点配置 ${filePath} 是空的。`);
    i += 1;
    const previous = seen.get(filePath);
    if (previous !== undefined) {
      if (previous !== body) throw new DeployError(`站点配置 ${filePath} 出现两份不同内容，拒绝修改。`);
      continue;
    }
    seen.set(filePath, body);
    found.push({ path: filePath, body });
  }
  return found;
}

export function planCacheInstall(stdout, stamp) {
  const found = parseDiscoverOutput(stdout);
  if (found.length === 0) {
    throw new DeployError(manualCacheInstructions());
  }
  const backupDir = cacheBackupDir(stamp);
  const vhosts = [];
  for (const item of found) {
    let planned;
    try {
      planned = ensureStaticCacheInclude(item.body);
    } catch (error) {
      if (error.code === 'NO_ROOT_BLOCK') continue;
      throw error;
    }
    vhosts.push({
      path: item.path,
      changed: planned.changed,
      body: planned.text,
      temp: `/tmp/fat-nginx-vhost-${vhosts.length}.conf`,
    });
  }
  if (vhosts.length === 0) {
    throw new DeployError(manualCacheInstructions());
  }
  return {
    stamp: assertStamp(stamp),
    backupDir,
    snippetRemote: SNIPPET_REMOTE,
    vhosts,
  };
}

export function cacheDiscoverScript() {
  const dirs = VHOST_DIRS.map((dir) => bashQuote(dir, '配置目录')).join(' ');
  const root = bashQuote(FIXED.webRoot, '网站根');
  return [
    'set -euo pipefail',
    'shopt -s nullglob',
    'found=0',
    `mark_a=${bashQuote('81.70.76.', '标记')}`,
    `mark_b=${bashQuote('42', '标记')}`,
    `name_a=${bashQuote('du', '标记')}`,
    `name_b=${bashQuote('hongbo', '标记')}`,
    `for dir in ${dirs}; do`,
    '  [ -d "$dir" ] || continue',
    '  for file in "$dir"/*.conf; do',
    '    [ -f "$file" ] || continue',
    '    real=$(readlink -f "$file" 2>/dev/null || true)',
    '    [ -n "$real" ] || continue',
    '    parent=$(dirname "$real")',
    '    base=$(basename "$real")',
    '    [ "$base" = "fat-static-cache.conf" ] && continue',
    '    case "$parent" in',
    '      /www/server/panel/vhost/nginx|/etc/nginx/conf.d|/etc/nginx/sites-enabled|/etc/nginx/sites-available|/usr/local/nginx/conf/vhost) ;;',
    '      *) continue ;;',
    '    esac',
    '    case "$base" in',
    '      *.conf) ;;',
    '      *) continue ;;',
    '    esac',
    `    if ! grep -F -q -- ${root} "$real"; then continue; fi`,
    '    if grep -F -q -- "${mark_a}${mark_b}" "$real" || grep -F -i -q -- "${name_a}${name_b}" "$real"; then',
    '      echo "站点配置含有禁止目标，拒绝继续" >&2',
    '      exit 1',
    '    fi',
    '    if base64 -w 0 </dev/null >/dev/null 2>&1; then',
    '      encoded=$(base64 -w 0 "$real")',
    '    else',
    '      encoded=$(base64 "$real" | tr -d "\\n")',
    '    fi',
    '    printf "CANDIDATE %s\\n" "$real"',
    '    printf "B64 %s\\n" "$encoded"',
    '    found=1',
    '  done',
    'done',
    'if [ "$found" -eq 0 ]; then echo CANDIDATE_NONE; fi',
    'echo DISCOVER_DONE',
    '',
  ].join('\n');
}

function nginxSelectLines(cfg) {
  const custom = String(cfg.nginxCmd || '').trim();
  const bin = custom ? custom.split(' ')[0] : '';
  const lines = [];
  if (bin) lines.push(`NGINX_BIN=${bashQuote(bin, 'nginx')}`);
  else lines.push("NGINX_BIN=''");
  lines.push('if [ -n "$NGINX_BIN" ]; then');
  lines.push('  NGINX="$NGINX_BIN"');
  lines.push('elif [ -x /www/server/nginx/sbin/nginx ]; then');
  lines.push('  NGINX=/www/server/nginx/sbin/nginx');
  lines.push('elif command -v nginx >/dev/null 2>&1; then');
  lines.push('  NGINX=$(command -v nginx)');
  lines.push('else');
  lines.push('  echo "找不到 nginx" >&2');
  lines.push('  exit 1');
  lines.push('fi');
  return lines;
}

export function nginxReloadScript(cfg) {
  return [
    'set -euo pipefail',
    ...nginxSelectLines(cfg),
    '"$NGINX" -t',
    '"$NGINX" -s reload',
    'echo NGINX_RELOADED',
    '',
  ].join('\n');
}

export function renderCacheApplyScript(cfg, plan) {
  const snippet = loadCacheSnippet();
  const backupDir = assertBackupPath(plan.backupDir);
  const custom = String(cfg.nginxCmd || '').trim();
  const nginxBin = custom ? bashQuote(custom.split(' ')[0], 'nginx') : "''";
  const changed = [];
  plan.vhosts.forEach((vhost, index) => {
    if (!vhost.changed) return;
    const temp = vhost.temp || `/tmp/fat-nginx-vhost-${index}.conf`;
    if (!/^\/tmp\/fat-nginx-vhost-\d+\.conf$/.test(temp)) {
      throw new DeployError(`站点配置临时路径不合法：${temp}`);
    }
    const backup = assertBackupPath(`${backupDir}/vhost-${index}.conf`);
    changed.push({
      path: assertVhostPath(vhost.path),
      temp,
      backup,
    });
  });
  const lines = [
    'set -euo pipefail',
    'umask 022',
    `NGINX_BIN=${nginxBin}`,
    `BACKUP_DIR=${bashQuote(backupDir, '缓存配置备份')}`,
    `SNIPPET_DEST=${bashQuote(SNIPPET_REMOTE, '缓存片段')}`,
    `WEB_ROOT=${bashQuote(FIXED.webRoot, '网站根')}`,
    `HOST=${bashQuote(ALLOWED_HOST, '主机')}`,
    'restore_cache_conf() {',
    '  set +e',
    '  if [ -f "$BACKUP_DIR/fat-static-cache.conf" ]; then cp -a "$BACKUP_DIR/fat-static-cache.conf" "$SNIPPET_DEST"',
    '  elif [ -f "$BACKUP_DIR/snippet.absent" ]; then rm -f "$SNIPPET_DEST"; fi',
  ];
  for (const vhost of changed) {
    lines.push(`  if [ -f ${bashQuote(vhost.backup, '站点配置备份')} ]; then cp -a ${bashQuote(vhost.backup, '站点配置备份')} ${bashQuote(vhost.path, '站点配置')}; fi`);
  }
  lines.push('  set -e');
  lines.push('}');
  lines.push('mkdir -p "$BACKUP_DIR" "$(dirname "$SNIPPET_DEST")"');
  lines.push('if [ -f "$SNIPPET_DEST" ]; then cp -a "$SNIPPET_DEST" "$BACKUP_DIR/fat-static-cache.conf"; else : > "$BACKUP_DIR/snippet.absent"; fi');
  lines.push(`cat > "$SNIPPET_DEST" << '${SNIPPET_END}'`);
  lines.push(snippet.replace(/\n$/, ''));
  lines.push(SNIPPET_END);
  lines.push('chmod 644 "$SNIPPET_DEST"');
  for (const vhost of changed) {
    lines.push(`cp -a ${bashQuote(vhost.path, '站点配置')} ${bashQuote(vhost.backup, '站点配置备份')}`);
    lines.push(`cp ${bashQuote(vhost.temp, '站点配置临时文件')} ${bashQuote(vhost.path, '站点配置')}`);
    lines.push(`chmod 644 ${bashQuote(vhost.path, '站点配置')}`);
  }
  lines.push('if [ -n "$NGINX_BIN" ]; then');
  lines.push('  NGINX="$NGINX_BIN"');
  lines.push('elif [ -x /www/server/nginx/sbin/nginx ]; then');
  lines.push('  NGINX=/www/server/nginx/sbin/nginx');
  lines.push('elif command -v nginx >/dev/null 2>&1; then');
  lines.push('  NGINX=$(command -v nginx)');
  lines.push('else');
  lines.push('  restore_cache_conf');
  lines.push('  echo "找不到 nginx，已还原缓存配置" >&2');
  lines.push('  exit 1');
  lines.push('fi');
  lines.push('if ! "$NGINX" -t; then');
  lines.push('  restore_cache_conf');
  lines.push('  echo "nginx 配置检测失败，已还原缓存片段和站点配置" >&2');
  lines.push('  exit 1');
  lines.push('fi');
  lines.push('if ! "$NGINX" -s reload; then');
  lines.push('  restore_cache_conf');
  lines.push('  "$NGINX" -s reload || true');
  lines.push('  echo "nginx 重载失败，已还原缓存片段和站点配置" >&2');
  lines.push('  exit 1');
  lines.push('fi');
  lines.push('echo "CACHE_BACKUP $BACKUP_DIR"');
  lines.push('if ! command -v curl >/dev/null 2>&1; then');
  lines.push('  echo PROBE_SKIP');
  lines.push('  echo CACHE_OK');
  lines.push('  exit 0');
  lines.push('fi');
  lines.push('asset=$(grep -oE \'/assets/[A-Za-z0-9._-]+\' "$WEB_ROOT/index.html" | head -n 1 || true)');
  lines.push('probe_path() {');
  lines.push('  target="$1"');
  lines.push('  if ! headers=$(curl -sI --max-time 15 --resolve "${HOST}:80:127.0.0.1" "http://${HOST}${target}" 2>/dev/null); then');
  lines.push('    echo "PROBE_ERROR ${target}"');
  lines.push('    return 0');
  lines.push('  fi');
  lines.push('  cc=$(printf \'%s\\n\' "$headers" | tr -d \'\\r\' | awk \'BEGIN{IGNORECASE=1; out=""} /^Cache-Control:/ { sub(/^[Cc]ache-[Cc]ontrol:[[:space:]]*/, ""); if (out != "") out = out ", "; out = out $0 } END { print out }\' || true)');
  lines.push('  if [ -z "$cc" ]; then echo "PROBE ${target} -"; else echo "PROBE ${target} ${cc}"; fi');
  lines.push('}');
  lines.push('probe_path /');
  lines.push('probe_path /index.html');
  lines.push('if [ -n "$asset" ]; then probe_path "$asset"; else echo PROBE_ERROR /assets; fi');
  lines.push('echo CACHE_OK');
  lines.push('');
  return lines.join('\n');
}

export function assertCacheProbeText(stdout) {
  const text = String(stdout ?? '');
  if (!text.includes('CACHE_OK')) {
    throw new DeployError('缓存配置没有完成。');
  }
  if (text.includes('PROBE_SKIP')) return { skipped: true, probes: [] };
  const probes = [];
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^PROBE (\S+) (.*)$/);
    if (!match) continue;
    probes.push({ path: match[1], cacheControl: match[2] === '-' ? '' : match[2].trim() });
  }
  if (probes.length === 0) {
    if (text.includes('PROBE_ERROR')) return { skipped: true, probes: [] };
    throw new DeployError('静态发布没有返回缓存探测结果。');
  }
  let sawHtml = false;
  let sawAsset = false;
  for (const probe of probes) {
    if (probe.path === '/' || probe.path === '/index.html') {
      sawHtml = true;
      assertHtmlCacheControl(probe.cacheControl, `服务器本机 ${probe.path}`);
    } else if (probe.path.startsWith('/assets/')) {
      sawAsset = true;
      assertAssetCacheControl(probe.cacheControl, `服务器本机 ${probe.path}`);
    }
  }
  if (!sawHtml) {
    if (text.includes('PROBE_ERROR')) return { skipped: true, probes };
    throw new DeployError('缓存探测缺少入口文档。');
  }
  if (!sawAsset && !text.includes('PROBE_ERROR')) {
    throw new DeployError('缓存探测缺少指纹资源。');
  }
  return { skipped: false, probes };
}

export function cacheRollback(backupDir) {
  return [
    '静态缓存配置回滚：',
    `  备份目录 ${backupDir}`,
    '  若其中有 vhost-*.conf，按发布日志里的原路径拷回。',
    '  若有 fat-static-cache.conf，拷回缓存片段路径；若只有 snippet.absent，删除现有片段即可。',
    '  然后执行 nginx -t 与 nginx -s reload。',
  ].join('\n');
}
