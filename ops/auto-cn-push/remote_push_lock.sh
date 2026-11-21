#!/usr/bin/env bash
# 在执行端用 flock 独占锁。YuePC 与 MSI 的新推送脚本通过 SSH 调用它。
# 现网旧脚本不调用本文件，因此在那些脚本被换上之前，这把锁不会生效。
set -euo pipefail
LOCK="${1:?lock path required}"
shift || true
mkdir -p "$(dirname "$LOCK")"
exec 8>"$LOCK"
if ! flock -n 8; then
  echo "REMOTE_PUSH_LOCK_HELD"
  exit 9
fi
if [[ "$#" -gt 0 ]]; then
  "$@"
fi
