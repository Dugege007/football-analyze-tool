#!/usr/bin/env bash
# 真实推送入口：先按包内已经写好的绑定授权，再提交。授权失败就不提交，也不会改绑。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/authorize_push_baseline.sh" "$@"
exec bash "$HERE/invoke_cn_push.sh" "$@"
