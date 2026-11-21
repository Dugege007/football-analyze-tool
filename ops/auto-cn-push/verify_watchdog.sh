#!/usr/bin/env bash
# 检查看门狗接线是否只装了一行，并做一次不拉起进程的判定。
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
if [[ -n "${AUTO_CN_PUSH_CRON_FILE:-}" ]]; then
  COUNT="$(grep -c "$MARKER" "$AUTO_CN_PUSH_CRON_FILE" || true)"
else
  COUNT="$(crontab -l 2>/dev/null | grep -c "$MARKER" || true)"
fi
if [[ "$COUNT" != "1" ]]; then
  echo "VERIFY_FAIL cron_count=$COUNT"
  exit 1
fi
"$PY" "$SCRIPT_DIR/watchdog.py" --home "$HOME_DIR" --decide-only --skip-scan >/dev/null
echo "VERIFY_OK"
