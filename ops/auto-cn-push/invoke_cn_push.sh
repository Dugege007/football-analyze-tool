#!/usr/bin/env bash
# 客户端推送入口。先拿投放目录锁，再读包，然后用一次 SSH 会话把 gz 送进服务器事务。
# 回执只采用服务器打印的 COMMIT_JSON，不修改已有回执文件的内容。
set -euo pipefail
DIR=""
SSH_TARGET="${FAT_CN_SSH_TARGET:-}"
SSH_KEY="${FAT_CN_SSH_KEY:-}"
SKIP_READY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --ssh-target) SSH_TARGET="$2"; shift 2 ;;
    --ssh-key) SSH_KEY="$2"; shift 2 ;;
    --skip-ready) SKIP_READY=1; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
if [[ -z "$DIR" ]]; then
  echo "missing --dir" >&2
  exit 2
fi
mkdir -p "$DIR"
exec 9>"$DIR/drop.operation.lock"
if ! flock -n 9; then
  echo "DROP_LOCK_HELD" >&2
  exit 11
fi
GZ="$DIR/app_latest.db.gz"
SHA="$DIR/app_latest.db.sha256"
GZSHA="$DIR/app_latest.db.gz.sha256"
READY="$DIR/app_latest.ready"
META="$DIR/meta.json"
RECEIPT="$DIR/LAST_PUSHED.json"
for need in "$GZ" "$SHA"; do
  if [[ ! -f "$need" ]]; then
    echo "missing $need" >&2
    exit 3
  fi
done
DB_SHA="$(awk '{print tolower($1)}' "$SHA")"
GZ_SHA="$(sha256sum "$GZ" | awk '{print tolower($1)}')"
if [[ -f "$GZSHA" ]]; then
  WANT="$(awk '{print tolower($1)}' "$GZSHA")"
  if [[ "$WANT" != "$GZ_SHA" ]]; then
    echo "gz sha mismatch" >&2
    exit 4
  fi
fi
if [[ "$SKIP_READY" -ne 1 ]]; then
  if [[ ! -f "$READY" ]]; then
    echo "missing ready" >&2
    exit 3
  fi
  python3 - "$READY" "$DB_SHA" "$GZ_SHA" <<'PY'
import json, sys
ready = json.loads(open(sys.argv[1], encoding="utf-8-sig").read())
db, gz = sys.argv[2], sys.argv[3]
if str(ready.get("db_sha256") or "").lower() not in ("", db):
    sys.exit("ready db mismatch")
if str(ready.get("gz_sha256") or "").lower() not in ("", gz):
    sys.exit("ready gz mismatch")
PY
fi
JC="unknown"
if [[ -f "$META" ]]; then
  JC="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8-sig")).get("jc_day") or "unknown")' "$META")"
fi
PRED=""
if [[ -f "$RECEIPT" ]]; then
  cp -a "$RECEIPT" "$RECEIPT.previous"
  PRED="$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1],encoding="utf-8-sig")).get("db_sha256") or "").lower())' "$RECEIPT")"
fi
HEADER="$(python3 - "$JC" "$DB_SHA" "$GZ_SHA" "$PRED" <<'PY'
import json, sys
print(json.dumps({
    "jc_day": sys.argv[1],
    "db_sha256": sys.argv[2],
    "gz_sha256": sys.argv[3],
    "predecessor_db_sha256": sys.argv[4],
}, ensure_ascii=False))
PY
)"
HERE="$(cd "$(dirname "$0")" && pwd)"
LOCK="${FAT_CN_LOCK:-/run/fat-cn-push.lock}"
script="$(cat "$HERE/cn_push_transaction.sh")"
payload="$(mktemp)"
trap 'rm -f "$payload"' EXIT
{
  printf '%s\n' "$HEADER"
  cat "$GZ"
} > "$payload"
# 远端没有脚本文件。把事务正文放进一次 flock，锁要盖住校验、替换、健康检查和提交。
remote_cmd="$(python3 - "$LOCK" "$script" "${FAT_CN_ROOT:-/opt/football-data}" "${FAT_CN_PAUSE_SEC:-}" "${FAT_CN_HEALTH_CMD:-}" <<'PY'
import shlex, sys
lock, script, root, pause, health = sys.argv[1:]
env = " ".join([
    "FAT_CN_ROOT=" + shlex.quote(root),
    "FAT_CN_PAUSE_SEC=" + shlex.quote(pause),
    "FAT_CN_HEALTH_CMD=" + shlex.quote(health),
])
body = """set -euo pipefail
LOCK=%s
mkdir -p "$(dirname "$LOCK")"
exec 8>"$LOCK"
if ! flock -n 8; then echo REMOTE_PUSH_LOCK_HELD >&2; exit 9; fi
exec bash -c %s
""" % (shlex.quote(lock), shlex.quote(script))
print(env + " bash -c " + shlex.quote(body))
PY
)"
set +e
if [[ -n "$SSH_TARGET" ]]; then
  ssh -T -o BatchMode=yes -o ConnectTimeout=30 ${SSH_KEY:+-i "$SSH_KEY"} "$SSH_TARGET" \
    "$remote_cmd" < "$payload" > "$DIR/commit.stdout"
  status=$?
else
  FAT_CN_ROOT="${FAT_CN_ROOT:-$DIR/remote-root}" \
    FAT_CN_PAUSE_SEC="${FAT_CN_PAUSE_SEC:-}" \
    FAT_CN_HEALTH_CMD="${FAT_CN_HEALTH_CMD:-}" \
    bash "$HERE/remote_push_lock.sh" "$LOCK" \
    < "$payload" > "$DIR/commit.stdout"
  status=$?
fi
set -e
if [[ "$status" -ne 0 ]]; then
  cat "$DIR/commit.stdout" >&2 || true
  exit "$status"
fi
python3 - "$DIR/commit.stdout" "$DB_SHA" "$GZ_SHA" "$RECEIPT" <<'PY'
import json, pathlib, sys
stdout, db, gz, receipt = sys.argv[1:]
line = ""
for raw in pathlib.Path(stdout).read_text(encoding="utf-8").splitlines():
    if raw.startswith("COMMIT_JSON "):
        line = raw[len("COMMIT_JSON "):]
if not line:
    sys.stderr.write("missing server commit\n")
    sys.exit(5)
commit = json.loads(line)
if commit.get("db_sha256") != db or commit.get("gz_sha256") != gz or commit.get("health_ok") is not True:
    sys.stderr.write("server commit does not match this package\n")
    sys.exit(5)
path = pathlib.Path(receipt)
path.write_text(json.dumps(commit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
rm -f "$READY"
echo "PUSH_COMMITTED"
