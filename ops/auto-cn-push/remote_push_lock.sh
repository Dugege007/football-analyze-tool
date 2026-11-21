#!/usr/bin/env bash
# 独占锁包住整个服务器事务。标准输入原样交给 cn_push_transaction.sh。
# 锁在进程退出时才释放，不会在单条远端命令结束后放开。
set -euo pipefail
LOCK="${1:?lock path required}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$(dirname "$LOCK")"
exec 8>"$LOCK"
if ! flock -n 8; then
  echo "REMOTE_PUSH_LOCK_HELD" >&2
  exit 9
fi
exec bash "$HERE/cn_push_transaction.sh"
