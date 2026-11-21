#!/usr/bin/env bash
# 安装看门狗 cron。重复执行只保留一行。
# 读 crontab 失败时停止，不把失败当成空表。
# 指向现网 ops 目录时必须显式 AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL=1。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${AUTO_CN_PUSH_HOME:-$SCRIPT_DIR}"
MARKER="# fat-auto-cn-push-watchdog"
PROD="/workspace/football-analyze-tool-ops/auto-cn-push-after-close-20261020"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/cron_table.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/load_push_env.sh"
RESOLVED="$(readlink -f "$HOME_DIR" 2>/dev/null || echo "$HOME_DIR")"
if [[ "$RESOLVED" == "$PROD" && "${AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL:-}" != "1" ]]; then
  echo "REFUSING_PRODUCTION_INSTALL"
  exit 4
fi
if ! load_push_env "$RESOLVED"; then
  exit 5
fi
ENV_FILE="$(readlink -f "$AUTO_CN_PUSH_ENV" 2>/dev/null || echo "$AUTO_CN_PUSH_ENV")"
mkdir -p "$RESOLVED/logs" "$RESOLVED/markers"
PREFIX=""
if [[ "$RESOLVED" == "$PROD" ]]; then
  PREFIX="AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL=1 "
fi
LINE="*/5 * * * * ${PREFIX}AUTO_CN_PUSH_ENV='$ENV_FILE' AUTO_CN_PUSH_HOME='$RESOLVED' '$SCRIPT_DIR/ensure_auto_cn_push_watch.sh' >> '$RESOLVED/logs/watchdog.cron.log' 2>&1 $MARKER"
if ! read_cron_table; then
  echo "CRONTAB_READ_FAILED"
  printf '%s\n' "${CRON_READ_ERROR:-}"
  exit 6
fi
BEFORE="$CRON_TABLE"
if ! read_cron_table; then
  echo "CRONTAB_READ_FAILED"
  exit 6
fi
if [[ "$CRON_TABLE" != "$BEFORE" ]]; then
  echo "CRONTAB_CONFLICT"
  exit 8
fi
FILTERED="$(printf '%s\n' "$BEFORE" | grep -v "$MARKER" || true)"
if [[ -n "$FILTERED" ]]; then
  NEW_TABLE="$(printf '%s\n%s' "$FILTERED" "$LINE")"
else
  NEW_TABLE="$LINE"
fi
write_cron_table "$NEW_TABLE"
if ! read_cron_table; then
  echo "CRONTAB_READ_FAILED_AFTER_WRITE"
  exit 6
fi
COUNT="$(printf '%s\n' "$CRON_TABLE" | grep -c "$MARKER" || true)"
if [[ "$COUNT" != "1" ]]; then
  if ! read_cron_table; then
    echo "CRONTAB_READ_FAILED"
    exit 6
  fi
  FILTERED="$(printf '%s\n' "$CRON_TABLE" | grep -v "$MARKER" || true)"
  if [[ -n "$FILTERED" ]]; then
    NEW_TABLE="$(printf '%s\n%s' "$FILTERED" "$LINE")"
  else
    NEW_TABLE="$LINE"
  fi
  write_cron_table "$NEW_TABLE"
  if ! read_cron_table; then
    echo "CRONTAB_READ_FAILED_AFTER_WRITE"
    exit 6
  fi
  COUNT="$(printf '%s\n' "$CRON_TABLE" | grep -c "$MARKER" || true)"
  if [[ "$COUNT" != "1" ]]; then
    echo "CRONTAB_VERIFY_FAILED count=$COUNT"
    exit 7
  fi
fi
mkdir -p "$RESOLVED/logs"
AUTO_CN_PUSH_ENV_FILE="$ENV_FILE" AUTO_CN_PUSH_STATE="$RESOLVED/logs/deploy_state.json" python3 - <<'PY'
import json, os
from pathlib import Path
state = {
    "env_file": os.environ["AUTO_CN_PUSH_ENV_FILE"],
    "windows_scripts_replaced": False,
    "crontab_restore": False,
    "note": "只增删本工具的 cron 行。不整表恢复，也不替换 Windows 脚本。",
}
Path(os.environ["AUTO_CN_PUSH_STATE"]).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
echo "INSTALLED"
