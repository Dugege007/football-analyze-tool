#!/usr/bin/env bash
# 独立看门狗入口。由 cron 每 5 分钟调用，不放进打包循环内部。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${AUTO_CN_PUSH_HOME:-$SCRIPT_DIR}"
if [[ -n "${AUTO_CN_PUSH_PYTHON:-}" ]]; then
  PY="$AUTO_CN_PUSH_PYTHON"
elif [[ -x /usr/bin/python3 ]]; then
  PY=/usr/bin/python3
else
  PY=python3
fi
exec "$PY" "$SCRIPT_DIR/watchdog.py" --home "$HOME_DIR" --script-dir "$SCRIPT_DIR"
