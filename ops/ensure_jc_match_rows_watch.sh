#!/usr/bin/env bash
# 竞彩场次行看门狗。建议 cron 每数分钟调用一次（在 football-analyze-tool 仓库根，且 api/data
# 已链到正式数据目录，或 MA_API_ROOT 指向 match-analysis-api）：
#
#   */5 * * * * /path/to/football-analyze-tool/ops/ensure_jc_match_rows_watch.sh >> api/logs/jc_match_rows_watch.cron.log 2>&1
#
# 进程不在则拉起 `build_jc_match_rows.py ensure-daemon`；仍占锁但心跳过期则先停再拉起。
# 竞彩 matches 行只走这条 ensure 路径。不要设置 LIVE_CAPTURE_CREATE_MATCHES（那不是 JC 补场入口，默认保持关闭）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${JC_MATCH_ROWS_PYTHON:-python3}"
exec "$PY" api/scripts/build_jc_match_rows.py ensure-watch "$@"
