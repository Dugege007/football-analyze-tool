import net from 'node:net';
import { ALLOWED_HOST, DeployError } from './assert-host.mjs';
import { bashQuote } from './bash.mjs';
import { assertAssetCacheControl, assertHtmlCacheControl, normalizeAssetPath } from './nginx-cache.mjs';

export function shanghaiDate(now = new Date()) {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now);
}

export function preflightScript() {
  return [
    'set -euo pipefail',
    'echo SSH_OK',
    'if [ -f /opt/football-data/CN_DEPLOY_HOST ]; then',
    "  got=$(tr -d '[:space:]' < /opt/football-data/CN_DEPLOY_HOST)",
    `  if [ "$got" != ${bashQuote(ALLOWED_HOST, '主机标记')} ]; then`,
    '    echo "远端主机标记与大陆站不一致，拒绝发布" >&2',
    '    exit 1',
    '  fi',
    '  echo MARKER_OK',
    'else',
    '  echo MARKER_ABSENT',
    'fi',
    '',
  ].join('\n');
}

export function healthScript(cfg) {
  const py = `${cfg.venv}/bin/python`;
  return [
    'set -euo pipefail',
    'if command -v curl >/dev/null 2>&1; then',
    '  curl -fsS --max-time 10 http://127.0.0.1:8787/health',
    'else',
    `  ${bashQuote(py, '虚拟环境解释器')} -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=10).read().decode())'`,
    'fi',
    '',
  ].join('\n');
}

export function listenScript() {
  return [
    'set +e',
    'if command -v ss >/dev/null 2>&1; then',
    '  ss -ltn | grep 8787',
    'else',
    '  netstat -ltn | grep 8787',
    'fi',
    'exit 0',
    '',
  ].join('\n');
}

export function dbHashScript(cfg) {
  return [
    'set -euo pipefail',
    `sha256sum ${bashQuote(cfg.dbPath, '正式库')}`,
    '',
  ].join('\n');
}

export function assertHealthJson(text) {
  let data;
  try {
    data = JSON.parse(String(text).trim());
  } catch {
    throw new DeployError('健康检查返回的不是 JSON。');
  }
  if (!data || data.ok !== true) {
    throw new DeployError('健康检查 ok 不为真。');
  }
  return data;
}

export function assertListenText(text) {
  const hits = String(text).split(/\r?\n/).map((line) => line.trim()).filter((line) => line.includes(':8787'));
  if (hits.length === 0) {
    throw new DeployError('远端没有在 8787 端口监听。');
  }
  for (const line of hits) {
    if (line.includes('0.0.0.0:8787') || line.includes('[::]:8787') || line.includes('*:8787')) {
      throw new DeployError('应用编程接口监听了非回环地址。必须只监听 127.0.0.1:8787，不得对公网开放。');
    }
  }
  if (!hits.some((line) => line.includes('127.0.0.1:8787'))) {
    throw new DeployError('没有看到 127.0.0.1:8787。');
  }
}

export function assertMatchesSample(text) {
  let data;
  try {
    data = JSON.parse(String(text));
  } catch {
    throw new DeployError('赛程接口返回的不是 JSON。');
  }
  if (!data || !Array.isArray(data.items)) {
    throw new DeployError('赛程接口缺少 items 数组。');
  }
  if (data.items.length === 0) return { sampled: false, count: 0 };
  const raw = JSON.stringify(data.items[0]);
  if (!raw.includes('"home_team_canonical"') || !raw.includes('"home_team_pending_confirm"')) {
    throw new DeployError('赛程接口没有规范名或待确认字段。');
  }
  return { sampled: true, count: data.items.length };
}

export function tcpClosed(host, port, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const socket = net.connect({ host, port });
    let settled = false;
    const finish = (closed) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(closed);
    };
    socket.setTimeout(timeoutMs);
    socket.on('connect', () => finish(false));
    socket.on('timeout', () => finish(true));
    socket.on('error', () => finish(true));
  });
}

async function fetchText(url) {
  let response;
  try {
    response = await fetch(url, {
      signal: AbortSignal.timeout(20000),
      redirect: 'follow',
      cache: 'no-store',
    });
  } catch (error) {
    throw new DeployError(`请求 ${url} 失败：${error.message || error}`);
  }
  const text = await response.text();
  return {
    status: response.status,
    text,
    cacheControl: response.headers.get('cache-control') || '',
  };
}

async function fetchHead(url) {
  let response;
  try {
    response = await fetch(url, {
      method: 'HEAD',
      redirect: 'follow',
      cache: 'no-store',
      signal: AbortSignal.timeout(20000),
    });
  } catch (error) {
    throw new DeployError(`请求 ${url} 失败：${error.message || error}`);
  }
  if (response.status === 405 || response.status === 501) {
    response = await fetch(url, {
      method: 'GET',
      redirect: 'follow',
      cache: 'no-store',
      signal: AbortSignal.timeout(20000),
    });
    await response.body?.cancel();
  }
  return {
    status: response.status,
    cacheControl: response.headers.get('cache-control') || '',
  };
}

export function describeVerify(cfg, state) {
  const lines = [
    `验收：登录 ${cfg.host} 并核对主机标记文件（若存在）`,
  ];
  if (cfg.modes.includes('static')) {
    const finger = state.assetRefs[0]
      ? `，并包含 ${state.assetRefs[0]}`
      : '（构建后核对首页里的资源指纹）';
    lines.push(`验收：公网 http://${cfg.host}/ 返回 200${finger}`);
    lines.push('验收：公网 / 与 /index.html 为 no-cache 或 max-age=0，不得带大于 0 的 max-age');
    lines.push('验收：公网 /assets/ 指纹文件至少缓存 30 天');
  }
  if (cfg.modes.includes('api') || cfg.modes.includes('db')) {
    lines.push('验收：服务器本机 http://127.0.0.1:8787/health 的 ok 为真');
    lines.push(`验收：公网 http://${cfg.host}/api/health 的 ok 为真`);
    lines.push('验收：监听地址只有 127.0.0.1:8787');
  }
  if (cfg.modes.includes('api')) {
    lines.push(`验收：抽样 http://${cfg.host}/api/matches?date=${shanghaiDate()}&scope=jingcai`);
  }
  if (cfg.modes.includes('db')) {
    lines.push('验收：远端正式库 SHA256 与本机一致');
  }
  lines.push(`验收：公网 ${cfg.host}:8787 应连不上`);
  return lines;
}

export async function runVerify(ctx) {
  const { cfg, runner, state, log } = ctx;
  for (const line of describeVerify(cfg, state)) log(line);
  if (cfg.dryRun || cfg.printPlan) return;

  const preflight = await runner.ssh(preflightScript(), '验收：确认 SSH 与主机标记');
  if (!preflight.stdout.includes('SSH_OK')) {
    throw new DeployError('SSH 预检没有返回 SSH_OK。');
  }

  if (cfg.modes.includes('api') || cfg.modes.includes('db')) {
    const health = await runner.ssh(healthScript(cfg), '验收：服务器本机健康检查');
    const healthJson = assertHealthJson(health.stdout);
    state.healthVersion = healthJson.version || '';
    const listen = await runner.ssh(listenScript(), '验收：确认只监听 127.0.0.1:8787');
    assertListenText(listen.stdout);
    const pub = await fetchText(`http://${cfg.host}/api/health`);
    if (pub.status !== 200) throw new DeployError(`公网 /api/health 状态码 ${pub.status}`);
    assertHealthJson(pub.text);
  }

  if (cfg.modes.includes('static')) {
    const home = await fetchText(`http://${cfg.host}/`);
    if (home.status !== 200) throw new DeployError(`公网首页状态码 ${home.status}`);
    const finger = state.assetRefs[0];
    if (!finger) throw new DeployError('没有资源指纹，无法核对首页。');
    if (!home.text.includes(finger)) {
      throw new DeployError(`公网首页没有本次构建的资源 ${finger}`);
    }
    const indexHead = await fetchHead(`http://${cfg.host}/index.html`);
    if (indexHead.status !== 200) throw new DeployError(`公网 /index.html 状态码 ${indexHead.status}`);
    assertHtmlCacheControl(home.cacheControl, '公网 /');
    assertHtmlCacheControl(indexHead.cacheControl, '公网 /index.html');
    const assetPath = normalizeAssetPath(finger);
    const assetHead = await fetchHead(`http://${cfg.host}${assetPath}`);
    if (assetHead.status !== 200) throw new DeployError(`公网资源 ${assetPath} 状态码 ${assetHead.status}`);
    assertAssetCacheControl(assetHead.cacheControl, `公网 ${assetPath}`);
    log('验收：入口为短缓存，指纹资源为长缓存');
  }

  if (cfg.modes.includes('api')) {
    const day = shanghaiDate();
    const url = `http://${cfg.host}/api/matches?date=${day}&scope=jingcai`;
    const sample = await fetchText(url);
    if (sample.status !== 200) throw new DeployError(`赛程抽样状态码 ${sample.status}`);
    const parsed = assertMatchesSample(sample.text);
    if (parsed.sampled) log(`验收：赛程抽样 ${parsed.count} 场，已看到规范名与待确认字段`);
    else log(`验收：${day} 没有竞彩赛程，跳过字段抽样`);
  }

  if (cfg.modes.includes('db')) {
    const remote = await runner.ssh(dbHashScript(cfg), '验收：核对远端正式库哈希');
    const got = remote.stdout.trim().split(/\s+/)[0];
    if (got !== state.dbHash) {
      throw new DeployError('远端正式库 SHA256 与本机不一致。');
    }
  }

  const closed = await tcpClosed(cfg.host, 8787, 5000);
  if (!closed) {
    throw new DeployError(`公网 ${cfg.host}:8787 可以连上。不得对公网开放 8787。`);
  }
  log('验收：公网 8787 未开放');
}
