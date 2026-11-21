#!/usr/bin/env bash
# 验收配置、循环健康和扫描。任一不健康都以非 0 退出并打印原因。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${AUTO_CN_PUSH_HOME:-$SCRIPT_DIR}"
MARKER="# fat-auto-cn-push-watchdog"
if [[ -n "${AUTO_CN_PUSH_PYTHON:-}" ]]; then
  PY="$AUTO_CN_PUSH_PYTHON"
elif [[ -x /usr/bin/python3 ]]; then
  PY=/usr/bin/python3
else
  PY=python3
fi
if [[ ! -x "$SCRIPT_DIR/ensure_auto_cn_push_watch.sh" ]]; then
  echo "VERIFY_FAIL watch script not executable"
  exit 1
fi
# shellcheck disable=SC1091
source "$SCRIPT_DIR/cron_table.sh"
if ! read_cron_table; then
  echo "CRONTAB_READ_FAILED"
  printf '%s\n' "${CRON_READ_ERROR:-}"
  exit 6
fi
exec "$PY" "$SCRIPT_DIR/verify_status.py" --home "$HOME_DIR" --cron-text "$CRON_TABLE" --marker "$MARKER"
