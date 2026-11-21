#!/usr/bin/env bash
# 安装器在读取包、sidecar、ready、expect、meta 之前调用。
# 占住与推送相同的 drop.operation.lock，直到标准输入关闭。
set -euo pipefail
if ! command -v flock >/dev/null 2>&1; then
  echo "DEPENDENCY_MISSING flock" >&2
  exit 20
fi
DIR="${1:?drop dir required}"
mkdir -p "$DIR"
exec 9>"$DIR/drop.operation.lock"
if ! flock -n 9; then
  echo "DROP_LOCK_HELD" >&2
  exit 11
fi
echo "DROP_LOCK_HELD_OK"
cat >/dev/null
