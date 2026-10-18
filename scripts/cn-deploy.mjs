#!/usr/bin/env node
/**
 * 大陆站一键发布。只允许 124.221.56.38。
 * 入口是仓库根的 deploy.bat / deploy.ps1，也可以直接 node scripts/cn-deploy.mjs。
 */
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline/promises';
import { fileURLToPath } from 'node:url';
import { DeployError, assertNotCi, normalizeConfig } from './cn-deploy-lib/assert-host.mjs';
import { loadEnvFile } from './cn-deploy-lib/load-env.mjs';
import { runApi } from './cn-deploy-lib/mode-api.mjs';
import { runDb } from './cn-deploy-lib/mode-db.mjs';
import { runStatic } from './cn-deploy-lib/mode-static.mjs';
import { createRunner } from './cn-deploy-lib/ssh.mjs';
import { preflightScript, runVerify } from './cn-deploy-lib/verify.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export function parseArgs(argv) {
  const out = {
    yes: false,
    dryRun: false,
    skipBuild: false,
    printPlan: false,
    help: false,
    modes: '',
    envFile: '',
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--yes') out.yes = true;
    else if (arg === '--dry-run') out.dryRun = true;
    else if (arg === '--skip-build') out.skipBuild = true;
    else if (arg === '--print-plan') out.printPlan = true;
    else if (arg === '--help' || arg === '-h') out.help = true;
    else if (arg === '--modes') {
      out.modes = argv[i + 1] ?? '';
      i += 1;
      if (!out.modes || out.modes.startsWith('--')) throw new DeployError('--modes 需要值，例如 static,api');
    } else if (arg.startsWith('--modes=')) out.modes = arg.slice('--modes='.length);
    else if (arg === '--env-file') {
      out.envFile = argv[i + 1] ?? '';
      i += 1;
      if (!out.envFile || out.envFile.startsWith('--')) throw new DeployError('--env-file 需要路径');
    } else if (arg.startsWith('--env-file=')) out.envFile = arg.slice('--env-file='.length);
    else throw new DeployError(`未知参数 ${arg}。可用 --help 查看。`);
  }
  return out;
}

export function makeStamp(date = new Date()) {
  return date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

export function formatPlan(cfg, { skipBuild }) {
  const lines = [
    '大陆站发布计划',
    `目标主机：${cfg.host}`,
    `登录用户：${cfg.sshUser}`,
    `发布模式：${cfg.modes.join(', ')}`,
    `网站根：${cfg.webRoot}`,
    `应用根：${cfg.appRoot}`,
    `服务单元：${cfg.unit}（只重启，不修改 User）`,
    `监听地址：${cfg.listen}（不会对公网开放 8787）`,
    cfg.modes.includes('db')
      ? `正式库：将覆盖远端正式库 ${cfg.dbPath}`
      : '正式库：本次不覆盖',
  ];
  if (cfg.modes.includes('static')) {
    lines.push('静态缓存：入口文档 no-cache，/assets/ 长期缓存，不给整站统一长缓存');
  }
  if (cfg.modes.includes('db')) lines.push(`本机正式库：${cfg.dbLocalPath}`);
  lines.push(`干跑：${cfg.dryRun ? '是' : '否'}`);
  lines.push(`跳过构建：${skipBuild ? '是' : '否'}`);
  lines.push('环境文件：不上传、不覆盖 /opt/football-data/env');
  lines.push('备份：静态、接口代码、正式库各保留最近 5 份，位于 /opt/football-data/backups');
  if (!cfg.sshKey) lines.push('私钥：未填写。实跑前必须把 DEPLOY_SSH_KEY 设为本机 id_ed25519 的绝对路径。');
  else lines.push(`私钥：${cfg.sshKey}`);
  return lines.join('\n');
}

function helpText() {
  return [
    '用法：node scripts/cn-deploy.mjs [选项]',
    '  --dry-run       只打印计划与远程步骤，不连接 SSH，不上传，不重启',
    '  --skip-build    静态模式使用已有 web/dist',
    '  --modes a,b     覆盖 DEPLOY_MODES，可选 static、api、db',
    '  --yes           跳过交互确认（仍会打印主机与模式）',
    '  --print-plan    只打印计划后退出',
    '  --env-file 路径  默认读取仓库根 .env.deploy',
    '目标主机只能是 124.221.56.38。',
  ].join('\n');
}

function assertKeyFile(cfg) {
  if (!cfg.sshKey) {
    throw new DeployError('DEPLOY_SSH_KEY 为空。YuePC 一般为 C:\\Users\\Administrator\\.ssh\\id_ed25519，MSI 一般为 C:\\Users\\win10\\.ssh\\id_ed25519。');
  }
  if (!fs.existsSync(cfg.sshKey) || !fs.statSync(cfg.sshKey).isFile()) {
    throw new DeployError(`私钥文件不存在：${cfg.sshKey}`);
  }
}

async function confirmInteractive(cfg) {
  if (cfg.modes.includes('db')) {
    console.log('请再次确认：这次会覆盖远端正式库。');
  }
  if (!process.stdin.isTTY) {
    throw new DeployError('当前不是交互终端。确认发布请加 --yes；不想发布就不要加 --yes。');
  }
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = (await rl.question('输入 Y 继续，其他键取消：')).trim();
    if (answer !== 'Y' && answer !== 'y') {
      console.log('已取消，未发布。');
      return false;
    }
  } finally {
    rl.close();
  }
  return true;
}

async function main(argv) {
  const nodeMajor = Number(process.versions.node.split('.')[0]);
  if (nodeMajor < 20) throw new DeployError(`需要 Node.js 20 或更高版本，当前为 ${process.versions.node}。`);
  const args = parseArgs(argv);
  if (args.help) {
    console.log(helpText());
    return 0;
  }
  const envFile = args.envFile
    ? path.resolve(args.envFile)
    : path.join(repoRoot, '.env.deploy');
  const raw = loadEnvFile(envFile);
  const cfg = normalizeConfig(raw, {
    repoRoot,
    modesOverride: args.modes,
    dryRun: args.dryRun,
    printPlan: args.printPlan,
  });
  assertNotCi(cfg.dryRun || cfg.printPlan);
  const state = { assetRefs: [], dbHash: '', rollbacks: [], healthVersion: '' };
  console.log(formatPlan(cfg, { skipBuild: args.skipBuild }));
  if (cfg.printPlan) return 0;
  if (cfg.dryRun) {
    if (!cfg.sshKey) console.log('私钥未填写，实跑会失败。');
    console.log('干跑：不连接 SSH，不上传，不重启。');
  } else {
    assertKeyFile(cfg);
    if (cfg.modes.includes('db')) {
      if (!fs.existsSync(cfg.dbLocalPath) || !fs.statSync(cfg.dbLocalPath).isFile()) {
        throw new DeployError(`本机正式库不存在：${cfg.dbLocalPath}`);
      }
    }
  }
  if (!args.yes && !cfg.dryRun) {
    const accepted = await confirmInteractive(cfg);
    if (!accepted) return 0;
  }
  const log = (line) => console.log(line);
  const runner = createRunner({ cfg, dryRun: cfg.dryRun, log });
  const ctx = {
    cfg,
    repoRoot,
    runner,
    stamp: makeStamp(),
    state,
    log,
    skipBuild: args.skipBuild,
  };
  try {
    if (!cfg.dryRun) {
      const preflight = await runner.ssh(preflightScript(), '预检：SSH 登录并核对主机标记');
      if (!preflight.stdout.includes('SSH_OK')) {
        throw new DeployError('SSH 预检没有返回 SSH_OK。');
      }
    } else {
      await runner.ssh(preflightScript(), '预检：以下登录检查只会在实跑时执行');
    }
    if (cfg.modes.includes('static')) await runStatic(ctx);
    if (cfg.modes.includes('api')) await runApi(ctx);
    if (cfg.modes.includes('db')) await runDb(ctx);
    await runVerify(ctx);
  } catch (error) {
    console.error(`发布失败：${error.message || error}`);
    if (state.rollbacks.length > 0) {
      console.error('已记录的备份与手工回滚要点（已成功的段不会自动回滚）：');
      for (const item of state.rollbacks) console.error(item);
    }
    if (process.env.DEPLOY_DEBUG === '1' && error.stack) console.error(error.stack);
    return 1;
  }
  if (cfg.dryRun) {
    console.log('干跑完成。未连接服务器，未上传，未重启。');
    return 0;
  }
  const summary = [
    '发布完成。',
    `模式：${cfg.modes.join(', ')}`,
    `时间：${ctx.stamp}`,
  ];
  if (state.assetRefs[0]) summary.push(`静态资源：${state.assetRefs[0]}`);
  if (state.healthVersion) summary.push(`健康检查版本：${state.healthVersion}`);
  if (state.dbHash) summary.push(`正式库哈希前缀：${state.dbHash.slice(0, 12)}`);
  console.log(summary.join('\n'));
  return 0;
}

const isDirect = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirect) {
  main(process.argv.slice(2)).then((code) => {
    process.exitCode = code;
  }).catch((error) => {
    console.error(`发布失败：${error.message || error}`);
    if (process.env.DEPLOY_DEBUG === '1' && error.stack) console.error(error.stack);
    process.exitCode = 1;
  });
}
