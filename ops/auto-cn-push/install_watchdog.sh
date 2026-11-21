#!/usr/bin/env bash
# 安装看门狗 cron。重复执行只保留一行。
# 指向现网 ops 目录时必须显式 AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL=1。
# 测试可设置 AUTO_CN_PUSH_CRON_FILE 写到临时文件，不调用 crontab。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${AUTO_CN_PUSH_HOME:-$SCRIPT_DIR}"
MARKER="# fat-auto-cn-push-watchdog"
PROD="/workspace/football-analyze-tool-ops/auto-cn-push-after-close-20261020"
RESOLVED="$(readlink -f "$HOME_DIR" 2>/dev/null || echo "$HOME_DIR")"
if [[ "$RESOLVED" == "$PROD" && "${AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL:-}" != "1" ]]; then
  echo "REFUSING_PRODUCTION_INSTALL"
  exit 4
fi
mkdir -p "$HOME_DIR/logs" "$HOME_DIR/markers"
# 只有这次安装明确指向现网目录时，cron 才带上允许标志。
# 否则看门狗在现网目录上会拒绝拉起。
PREFIX=""
if [[ "$RESOLVED" == "$PROD" ]]; then
  PREFIX="AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL=1 "
fi
LINE="*/5 * * * * ${PREFIX}AUTO_CN_PUSH_HOME='$RESOLVED' '$SCRIPT_DIR/ensure_auto_cn_push_watch.sh' >> '$RESOLVED/logs/watchdog.cron.log' 2>&1 $MARKER"
if [[ -n "${AUTO_CN_PUSH_CRON_FILE:-}" ]]; then
  touch "$AUTO_CN_PUSH_CRON_FILE"
  grep -v "$MARKER" "$AUTO_CN_PUSH_CRON_FILE" > "$AUTO_CN_PUSH_CRON_FILE.tmp" || true
  printf '%s\n' "$LINE" >> "$AUTO_CN_PUSH_CRON_FILE.tmp"
  mv "$AUTO_CN_PUSH_CRON_FILE.tmp" "$AUTO_CN_PUSH_CRON_FILE"
else
  (crontab -l 2>/dev/null | grep -v "$MARKER" || true; printf '%s\n' "$LINE") | crontab -
fi
echo "INSTALLED"
