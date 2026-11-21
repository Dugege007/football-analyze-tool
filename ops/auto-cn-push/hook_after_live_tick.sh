#!/usr/bin/env bash
# 可选：由 live_capture 包装器在 tick 之后调用。失败不影响采集。
# 打包锁与 pending 会挡住并发覆盖。不在这里拉起或结束打包循环。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${AUTO_CN_PUSH_HOME:-$SCRIPT_DIR}"
mkdir -p "$HOME_DIR/logs"
if [[ -n "${AUTO_CN_PUSH_PYTHON:-}" ]]; then
  PY="$AUTO_CN_PUSH_PYTHON"
elif [[ -x /usr/bin/python3 ]]; then
  PY=/usr/bin/python3
else
  PY=python3
fi
"$PY" "$SCRIPT_DIR/run_auto_cn_push.py" --home "$HOME_DIR" --prepare >>"$HOME_DIR/logs/hook.log" 2>&1 || true
