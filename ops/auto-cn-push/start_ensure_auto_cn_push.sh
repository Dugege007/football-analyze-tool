#!/usr/bin/env bash
# 启动打包循环。是否已在跑由看门狗的锁和启动身份判断，不用 kill -0 单独认 pid。
# pid 被无关进程复用时不结束那个进程。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${AUTO_CN_PUSH_HOME:-$SCRIPT_DIR}"
LOG="$HOME_DIR/logs"
mkdir -p "$LOG"
if [[ -n "${AUTO_CN_PUSH_PYTHON:-}" ]]; then
  PY="$AUTO_CN_PUSH_PYTHON"
elif [[ -x /usr/bin/python3 ]]; then
  PY=/usr/bin/python3
else
  PY=python3
fi
DECISION="$("$PY" "$SCRIPT_DIR/watchdog.py" --home "$HOME_DIR" --script-dir "$SCRIPT_DIR" --decide-only --skip-scan)"
ACTION="$(printf '%s' "$DECISION" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("action",""))')"
REASON="$(printf '%s' "$DECISION" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("reason",""))')"
if [[ "$ACTION" == "ok" ]]; then
  echo "already running reason=$REASON"
  exit 0
fi
if [[ "$ACTION" == "alert" ]]; then
  echo "UNSAFE_NOT_STARTED reason=$REASON"
  printf '%s\n' "$DECISION"
  exit 3
fi
if [[ "$ACTION" != "restart" ]]; then
  echo "UNSAFE_NOT_STARTED reason=$REASON"
  exit 3
fi
# restart：循环已死。即使 pid 文件指向仍活着的进程，也不向它发信号。
export AUTO_CN_PUSH_HOME="$HOME_DIR"
before_beat=0
if [[ -f "$LOG/heartbeat.json" ]]; then
  before_beat="$("$PY" -c 'import os,sys; print(os.stat(sys.argv[1]).st_mtime_ns)' "$LOG/heartbeat.json")"
fi
nohup "$SCRIPT_DIR/ensure_auto_cn_push_loop.sh" >>"$LOG/ensure.log" 2>&1 &
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.2
  if [[ -f "$LOG/heartbeat.json" ]]; then
    now_beat="$("$PY" -c 'import os,sys; print(os.stat(sys.argv[1]).st_mtime_ns)' "$LOG/heartbeat.json")"
  else
    now_beat=0
  fi
  if [[ -f "$LOG/ensure_auto_cn_push.pid" && "$now_beat" -gt "$before_beat" ]]; then
    pid="$(cat "$LOG/ensure_auto_cn_push.pid")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "started pid=$pid"
      exit 0
    fi
    echo "started pid=$pid (exited after writing heartbeat)"
    exit 0
  fi
done
echo "START_FAILED"
exit 1
