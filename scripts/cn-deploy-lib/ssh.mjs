import { spawn } from 'node:child_process';
import path from 'node:path';
import { DeployError, assertBackupPath, assertTempPath, assertSafeRemotePath, FIXED } from './assert-host.mjs';

function spawnCaptured(cmd, args, { input, quiet = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    const out = [];
    const err = [];
    child.stdout.on('data', (chunk) => {
      out.push(chunk);
      if (!quiet) process.stdout.write(chunk);
    });
    child.stderr.on('data', (chunk) => {
      err.push(chunk);
      process.stderr.write(chunk);
    });
    child.on('error', (error) => {
      if (error && error.code === 'ENOENT') {
        reject(new DeployError(`没有找到命令 ${cmd}。请先安装 OpenSSH 客户端。`));
        return;
      }
      reject(error);
    });
    child.on('close', (code) => {
      const stdout = Buffer.concat(out).toString('utf8');
      const stderr = Buffer.concat(err).toString('utf8');
      if (code !== 0) {
        const failure = new DeployError(`${cmd} 退出码 ${code}`);
        failure.stdout = stdout;
        failure.stderr = stderr;
        reject(failure);
        return;
      }
      resolve({ stdout, stderr, code: 0 });
    });
    if (input) child.stdin.write(input);
    child.stdin.end();
  });
}

function sshArgs(cfg) {
  return [
    '-i', cfg.sshKey,
    '-p', String(cfg.sshPort),
    '-o', 'BatchMode=yes',
    '-o', 'StrictHostKeyChecking=accept-new',
    '-o', 'ConnectTimeout=20',
    '-o', 'IdentitiesOnly=yes',
    '-o', 'ServerAliveInterval=15',
    `${cfg.sshUser}@${cfg.host}`,
  ];
}

function scpArgs(cfg) {
  return [
    '-i', cfg.sshKey,
    '-P', String(cfg.sshPort),
    '-o', 'BatchMode=yes',
    '-o', 'StrictHostKeyChecking=accept-new',
    '-o', 'ConnectTimeout=20',
    '-o', 'IdentitiesOnly=yes',
  ];
}

function assertUploadTarget(remotePath, purpose) {
  if (purpose === 'temp') return assertTempPath(remotePath);
  if (purpose === 'backup') return assertBackupPath(remotePath);
  if (purpose === 'db-incoming') {
    const text = assertSafeRemotePath(remotePath, '正式库 incoming');
    if (text !== FIXED.dbIncoming) {
      throw new DeployError(`正式库只能先上传到 ${FIXED.dbIncoming}。`);
    }
    return text;
  }
  throw new DeployError(`未知上传用途：${purpose}`);
}

export function createRunner({ cfg, dryRun, log = console.log, spawnImpl = spawnCaptured }) {
  return {
    async upload(localPath, remotePath, purpose) {
      const remote = assertUploadTarget(remotePath, purpose);
      const base = path.basename(localPath);
      if (dryRun) {
        log(`[干跑] 将上传 ${base} -> ${remote}`);
        return { stdout: 'DRY_RUN\n', stderr: '', code: 0 };
      }
      log(`上传 ${base} -> ${remote}`);
      const args = [
        ...scpArgs(cfg),
        localPath,
        `${cfg.sshUser}@${cfg.host}:${remote}`,
      ];
      return spawnImpl('scp', args);
    },
    async ssh(script, title, options = {}) {
      if (title) log(title);
      if (dryRun) {
        log('---- 干跑：远程脚本（不执行）----');
        log(String(script).trimEnd());
        log('---- 干跑结束 ----');
        return { stdout: 'DRY_RUN\n', stderr: '', code: 0 };
      }
      const args = [...sshArgs(cfg), 'bash', '-s'];
      return spawnImpl('ssh', args, { input: script, quiet: Boolean(options.quiet) });
    },
  };
}
