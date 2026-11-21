#!/usr/bin/env bash
# 明确授权：把当前服务器 LAST_COMMIT 冻结到这个 gz。已冻结的记录不会被改写。
exec bash "$(cd "$(dirname "$0")" && pwd)/invoke_cn_push.sh" --authorize "$@"
