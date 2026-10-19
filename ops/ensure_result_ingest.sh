#!/usr/bin/env bash
# 赛果入库看门狗。建议定时任务每数分钟调用一次（在 football-analyze-tool 仓库根）：
#
#   */5 * * * * /path/to/football-analyze-tool/ops/ensure_result_ingest.sh >> /workspace/odds-data/backfill/result-ingest-2026-10-14/ensure_watch.log 2>&1
#
# 语义对齐竞彩场次行看门狗：输出目录的 daemon.lock 未占用则拉起单实例；已占用则不拉第二份。
# 安静成功退出 0。heartbeat.json 修改时间距今超过 600 秒且锁仍占用时，打印 HEARTBEAT_STALE 并退出 2（只告警，不结束进程、不二次拉起）。
# 需要启动但缺少 FIVEDOLLAR_FOOTBALL_API_KEY 时，打印 START_FAILED_NO_KEY 并退出 3。
# 取消 LIVE_CAPTURE_CREATE_MATCHES；不打开双写。启动前若正式库旁没有 FORMAL_MERGED.flag，则创建空文件。
#
# 密钥与路径不要提交进本仓库。依次读取：
#   $RESULT_INGEST_ENV
#   /workspace/football-analyze-tool-ops/result-ingest-watchdog/.env.result_ingest（本机过渡副本）
# 键名见 ops/.env.result_ingest.example：FIVEDOLLAR_FOOTBALL_API_KEY、APP_DB_PATH、FORMAL_MERGED。
# 合并之后，机上例行任务再改为调用本脚本；过渡环境文件继续留在仓库外。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${RESULT_INGEST_OUT:-/workspace/odds-data/backfill/result-ingest-2026-10-14}"
FLAG="${FORMAL_MERGED_FLAG:-/workspace/match-analysis-api/data/FORMAL_MERGED.flag}"
APP_DB="${APP_DB_PATH:-/workspace/match-analysis-api/data/app.db}"
LOG="$OUT/ensure_watch.log"
mkdir -p "$OUT"

ts() { date '+%Y-%m-%dT%H:%M:%S%z' | sed 's/\([0-9][0-9]\)$/:\1/'; }

pick_env() {
  local c
  for c in \
    "${RESULT_INGEST_ENV:-}" \
    /workspace/football-analyze-tool-ops/result-ingest-watchdog/.env.result_ingest
  do
    [ -n "$c" ] || continue
    if [ -f "$c" ]; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

pick_py() {
  if [ -x /workspace/odds-data/.venv-dc/bin/python ]; then
    printf '%s\n' /workspace/odds-data/.venv-dc/bin/python
    return 0
  fi
  if [ -x /workspace/odds-data/.venv-dc/bin/python3 ]; then
    printf '%s\n' /workspace/odds-data/.venv-dc/bin/python3
    return 0
  fi
  if [ -x "$ROOT/.venv/bin/python" ]; then
    printf '%s\n' "$ROOT/.venv/bin/python"
    return 0
  fi
  command -v python3
}

if [ ! -f "$ROOT/scripts/live/result_ingest.py" ]; then
  echo "$(ts) FAIL repo missing scripts/live/result_ingest.py under $ROOT" >> "$LOG"
  echo "START_FAILED_NO_REPO"
  exit 1
fi

PY="$(pick_py)" || {
  echo "$(ts) FAIL no python" >> "$LOG"
  echo "START_FAILED_NO_PY"
  exit 1
}

if [ ! -f "$FLAG" ]; then
  : > "$FLAG"
  echo "$(ts) created FORMAL_MERGED.flag at $FLAG" >> "$LOG"
fi

ENV_FILE=""
if ENV_FILE="$(pick_env)"; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi
export APP_DB_PATH="${APP_DB_PATH:-$APP_DB}"
export FORMAL_MERGED="${FORMAL_MERGED:-1}"
unset LIVE_CAPTURE_CREATE_MATCHES || true

cd "$ROOT" || {
  echo "$(ts) FAIL cd $ROOT" >> "$LOG"
  exit 1
}

# 看门狗自身防并发
exec 9>>"$OUT/ensure.lock"
if ! flock -n 9; then
  exit 0
fi

# 试抢 daemon.lock：抢到表示常驻进程没在跑
exec 8>>"$OUT/daemon.lock"
if flock -n 8; then
  flock -u 8
  if [ -z "${FIVEDOLLAR_FOOTBALL_API_KEY:-}" ]; then
    echo "$(ts) FAIL daemon not running and FIVEDOLLAR_FOOTBALL_API_KEY missing; not started" >> "$LOG"
    echo "START_FAILED_NO_KEY"
    exit 3
  fi
  nohup setsid "$PY" scripts/live/result_ingest.py daemon --out-dir "$OUT" \
    >> "$OUT/daemon.out" 2>&1 < /dev/null 8>&- 9>&- &
  echo "$(ts) started pid=$! repo=$ROOT py=$PY db=$APP_DB_PATH" >> "$LOG"
  exit 0
fi

echo "$(ts) running (daemon.lock held) repo=$ROOT" >> "$LOG"
HB="$OUT/heartbeat.json"
if [ -f "$HB" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$HB") ))
  if [ "$age" -gt 600 ]; then
    echo "$(ts) HEARTBEAT_STALE age=${age}s (daemon.lock held; NOT restarting)" >> "$LOG"
    echo "HEARTBEAT_STALE"
    exit 2
  fi
else
  echo "$(ts) HEARTBEAT_STALE heartbeat.json missing" >> "$LOG"
  echo "HEARTBEAT_STALE"
  exit 2
fi
exit 0
