import fs from 'node:fs';
import { DeployError } from './assert-host.mjs';

export function parseEnv(text) {
  const out = {};
  const body = String(text ?? '').replace(/^\uFEFF/, '');
  for (const rawLine of body.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq <= 0) continue;
    let key = line.slice(0, eq).trim();
    if (key.startsWith('export ')) key = key.slice(7).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"') && value.length >= 2)
      || (value.startsWith("'") && value.endsWith("'") && value.length >= 2)
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

export function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) {
    throw new DeployError(
      `没有找到 ${filePath}。请先复制 .env.deploy.example 为 .env.deploy，再填写私钥路径。该文件只留在本机。`,
    );
  }
  return parseEnv(fs.readFileSync(filePath, 'utf8'));
}
