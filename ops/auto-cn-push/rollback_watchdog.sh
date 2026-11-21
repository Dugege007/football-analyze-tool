#!/usr/bin/env bash
# 撤回看门狗 cron，并在备份校验通过时恢复安装前的整张表。
# 不删除 pending、staging、标记，也不改 Windows 上的推送脚本。
# 读 crontab 失败时停止。
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
CURRENT="$CRON_TABLE"
BACKUP="$RESOLVED/logs/crontab.before-install"
if [[ -f "$BACKUP" && -f "$BACKUP.sha256" ]]; then
  if ! sha256sum -c "$BACKUP.sha256" >/dev/null 2>&1; then
    echo "CRONTAB_BACKUP_MISMATCH"
    exit 7
  fi
  RESTORED="$(cat "$BACKUP")"
else
  RESTORED="$(printf '%s\n' "$CURRENT" | grep -v "$MARKER" || true)"
fi
if ! cron_preserved "$(printf '%s\n' "$CURRENT" | grep -v "$MARKER" || true)" "$RESTORED" "$MARKER"; then
  echo "CRONTAB_ROLLBACK_WOULD_DROP_JOBS"
  exit 7
fi
write_cron_table "$RESTORED"
if ! read_cron_table; then
  echo "CRONTAB_READ_FAILED_AFTER_ROLLBACK"
  exit 6
fi
if printf '%s\n' "$CRON_TABLE" | grep -q "$MARKER"; then
  echo "CRONTAB_ROLLBACK_STILL_PRESENT"
  exit 7
fi
mkdir -p "$RESOLVED/logs"
cat > "$RESOLVED/logs/ROLLBACK_DONE.json" <<EOF
{"cron_removed": true, "markers_kept": true, "pending_kept": true, "staging_kept": true, "windows_scripts_untouched": true}
EOF
echo "ROLLED_BACK"
