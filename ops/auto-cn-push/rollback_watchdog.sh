#!/usr/bin/env bash
# 只删除本工具的 cron 行。不恢复整张旧表，因此不会把别人已删除的任务加回去。
# 读表失败或写入前表已变化时停止，不做第二次之外的自动覆盖。
# 不删除 pending、staging、标记，也不改 Windows 脚本。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${AUTO_CN_PUSH_HOME:-$SCRIPT_DIR}"
MARKER="# fat-auto-cn-push-watchdog"
PROD="/workspace/football-analyze-tool-ops/auto-cn-push-after-close-20261020"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/cron_table.sh"
if [[ -d "$HOME_DIR" ]]; then
  RESOLVED="$(cd "$HOME_DIR" && pwd)"
else
  RESOLVED="$HOME_DIR"
fi
if [[ "$RESOLVED" == "$PROD" && "${AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL:-}" != "1" ]]; then
  echo "REFUSING_PRODUCTION_ROLLBACK"
  exit 4
fi
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
write_cron_table "$FILTERED"
if ! read_cron_table; then
  echo "CRONTAB_READ_FAILED_AFTER_ROLLBACK"
  exit 6
fi
if printf '%s\n' "$CRON_TABLE" | grep -q "$MARKER"; then
  if ! read_cron_table; then
    echo "CRONTAB_READ_FAILED"
    exit 6
  fi
  FILTERED="$(printf '%s\n' "$CRON_TABLE" | grep -v "$MARKER" || true)"
  write_cron_table "$FILTERED"
  if ! read_cron_table; then
    echo "CRONTAB_READ_FAILED_AFTER_ROLLBACK"
    exit 6
  fi
  if printf '%s\n' "$CRON_TABLE" | grep -q "$MARKER"; then
    echo "CRONTAB_ROLLBACK_STILL_PRESENT"
    exit 7
  fi
fi
mkdir -p "$RESOLVED/logs"
cat > "$RESOLVED/logs/ROLLBACK_DONE.json" <<EOF
{"cron_removed": true, "full_table_restored": false, "markers_kept": true, "pending_kept": true, "staging_kept": true, "windows_scripts_untouched": true}
EOF
echo "ROLLED_BACK"
