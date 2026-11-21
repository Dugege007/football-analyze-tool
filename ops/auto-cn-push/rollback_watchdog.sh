#!/usr/bin/env bash
# 撤回看门狗 cron。不停止已经在跑的打包循环。重复执行仍然成功。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${AUTO_CN_PUSH_HOME:-$SCRIPT_DIR}"
MARKER="# fat-auto-cn-push-watchdog"
PROD="/workspace/football-analyze-tool-ops/auto-cn-push-after-close-20261020"
if [[ -d "$HOME_DIR" ]]; then
  RESOLVED="$(cd "$HOME_DIR" && pwd)"
else
  RESOLVED="$HOME_DIR"
fi
if [[ "$RESOLVED" == "$PROD" && "${AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL:-}" != "1" ]]; then
  echo "REFUSING_PRODUCTION_ROLLBACK"
  exit 4
fi
if [[ -n "${AUTO_CN_PUSH_CRON_FILE:-}" ]]; then
  if [[ -f "$AUTO_CN_PUSH_CRON_FILE" ]]; then
    grep -v "$MARKER" "$AUTO_CN_PUSH_CRON_FILE" > "$AUTO_CN_PUSH_CRON_FILE.tmp" || true
    mv "$AUTO_CN_PUSH_CRON_FILE.tmp" "$AUTO_CN_PUSH_CRON_FILE"
  fi
else
  (crontab -l 2>/dev/null | grep -v "$MARKER" || true) | crontab -
fi
echo "ROLLED_BACK"
