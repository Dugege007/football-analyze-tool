import { DeployError } from './assert-host.mjs';

/** 只接受已经过白名单断言的短字符串，再包进单引号交给远端 bash。 */
export function bashQuote(value, label = '参数') {
  const text = String(value);
  if (!/^[A-Za-z0-9._/@:-]+$/.test(text)) {
    throw new DeployError(`${label}含有拒绝写入远程命令的字符。`);
  }
  return `'${text}'`;
}
