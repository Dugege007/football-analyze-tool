#!/usr/bin/env bash
# 正式库齐套打包循环。每轮写心跳，再 --prepare 与 --check-alerts。
# 进程活着期间一直持有日志锁。看门狗在本脚本之外，不要在这里自拉起。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${AUTO_CN_PUSH_HOME:-$SCRIPT_DIR}"
LOG="$HOME_DIR/logs"
mkdir -p "$LOG" "$HOME_DIR/markers"
LOCK="$LOG/ensure_auto_cn_push.lock"
INTERVAL="${AUTO_CN_PUSH_INTERVAL_SEC:-300}"
if [[ -n "${AUTO_CN_PUSH_PYTHON:-}" ]]; then
  PY="$AUTO_CN_PUSH_PYTHON"
elif [[ -x /usr/bin/python3 ]]; then
  PY=/usr/bin/python3
else
  PY=python3
fi
# shellcheck disable=SC1091
source "$SCRIPT_DIR/load_push_env.sh"
if ! load_push_env "$HOME_DIR"; then
  exit 5
fi
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "already running pid=$(cat "$LOG/ensure_auto_cn_push.pid" 2>/dev/null || echo ?)"
  exit 0
fi
"$PY" "$SCRIPT_DIR/write_heartbeat.py" --home "$HOME_DIR" --pid "$$" --phase starting --init-identity
echo "ensure_auto_cn_push start $(date '+%Y-%m-%dT%H:%M:%S%z') pid=$$" | tee -a "$LOG/ensure.log"
export AUTO_CN_PUSH_HOME="$HOME_DIR"
while true; do
  "$PY" "$SCRIPT_DIR/write_heartbeat.py" --home "$HOME_DIR" --pid "$$" --phase prepare
  "$PY" "$SCRIPT_DIR/run_auto_cn_push.py" --home "$HOME_DIR" --db "$APP_DB_PATH" --prepare >>"$LOG/cron.log" 2>&1 || true
  "$PY" "$SCRIPT_DIR/write_heartbeat.py" --home "$HOME_DIR" --pid "$$" --phase check-alerts
  "$PY" "$SCRIPT_DIR/run_auto_cn_push.py" --home "$HOME_DIR" --db "$APP_DB_PATH" --check-alerts >>"$LOG/alert.log" 2>&1 || true
  if [[ -f "$HOME_DIR/markers/pending_push.json" ]]; then
    cp -f "$HOME_DIR/markers/pending_push.json" "$LOG/NEED_YPC_PUSH.json"
  else
    rm -f "$LOG/NEED_YPC_PUSH.json"
  fi
  if [[ -f "$HOME_DIR/markers/ALERT_PENDING_TIMEOUT.json" ]]; then
    cp -f "$HOME_DIR/markers/ALERT_PENDING_TIMEOUT.json" "$LOG/ALERT_PENDING_TIMEOUT.json"
  else
    rm -f "$LOG/ALERT_PENDING_TIMEOUT.json"
  fi
  "$PY" "$SCRIPT_DIR/write_heartbeat.py" --home "$HOME_DIR" --pid "$$" --phase sleep
  if [[ "${AUTO_CN_PUSH_ONCE:-}" == "1" ]]; then
    exit 0
  fi
  sleep "$INTERVAL"
done
