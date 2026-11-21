#!/usr/bin/env bash
# 旧的每 5 分钟直接 --prepare 安装器。现网不用它。
# 默认拒绝，避免和打包循环变成两个 prepare 源。
# 只有 AUTO_CN_PUSH_ALLOW_LEGACY_CRON=1 才写入 crontab。
set -euo pipefail
if [[ "${AUTO_CN_PUSH_ALLOW_LEGACY_CRON:-}" != "1" ]]; then
  echo "REFUSING_LEGACY_PREPARE_CRON"
  echo "打包由 ensure_auto_cn_push_loop.sh 单源执行；恢复由 ensure_auto_cn_push_watch.sh 负责。"
  exit 4
fi
OPS="$(cd "$(dirname "$0")" && pwd)"
LOG="$OPS/logs/cron.log"
mkdir -p "$OPS/logs"
LINE="*/5 * * * * cd '$OPS' && /usr/bin/python3 '$OPS/run_auto_cn_push.py' --prepare >>'$LOG' 2>&1"
(crontab -l 2>/dev/null | grep -v 'run_auto_cn_push.py' || true; echo "$LINE") | crontab -
echo "installed:"; crontab -l | grep run_auto_cn_push || true
