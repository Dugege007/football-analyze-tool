#!/usr/bin/env bash
# 客户端推送入口。先拿投放目录锁，再读包，然后用一次 SSH 会话把 gz 送进服务器事务。
# 预期提交身份只来自已冻结的 push_baseline.json。不读取 LAST_PUSHED 来重绑前驱。
# 回执只采用服务器打印的 COMMIT_JSON。
set -euo pipefail
DIR=""
SSH_TARGET="${FAT_CN_SSH_TARGET:-}"
SSH_KEY="${FAT_CN_SSH_KEY:-}"
SKIP_READY=0
AUTHORIZE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --ssh-target) SSH_TARGET="$2"; shift 2 ;;
    --ssh-key) SSH_KEY="$2"; shift 2 ;;
    --skip-ready) SKIP_READY=1; shift ;;
    --authorize) AUTHORIZE=1; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
if [[ -z "$SSH_TARGET" && "${FAT_CN_ALLOW_TEST_LOCAL:-}" != "1" ]]; then
  echo "SSH_TARGET_REQUIRED" >&2
  exit 2
fi
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
BASELINE="$DIR/push_baseline.json"
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
if [[ "$AUTHORIZE" -ne 1 && "$SKIP_READY" -ne 1 ]]; then
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
EXPECTED=""
if [[ "$AUTHORIZE" -ne 1 ]]; then
  if [[ ! -f "$BASELINE" ]]; then
    echo "BASELINE_NOT_FROZEN" >&2
    exit 18
  fi
  EXPECTED="$(python3 - "$BASELINE" "$DB_SHA" "$GZ_SHA" <<'PY'
import json, sys
base = json.loads(open(sys.argv[1], encoding="utf-8-sig").read())
db, gz = sys.argv[2], sys.argv[3]
if str(base.get("db_sha256") or "").lower() != db or str(base.get("gz_sha256") or "").lower() != gz:
    sys.stderr.write("baseline package mismatch\n")
    sys.exit(4)
commit_id = str(base.get("expected_commit_id") or "")
if not commit_id:
    sys.stderr.write("BASELINE_NOT_FROZEN\n")
    sys.exit(18)
print(commit_id)
PY
)"
fi
OP="commit"
if [[ "$AUTHORIZE" -eq 1 ]]; then
  OP="authorize"
fi
HEADER="$(python3 - "$OP" "$JC" "$DB_SHA" "$GZ_SHA" "$EXPECTED" <<'PY'
import json, sys
print(json.dumps({
    "op": sys.argv[1],
    "jc_day": sys.argv[2],
    "db_sha256": sys.argv[3],
    "gz_sha256": sys.argv[4],
    "expected_commit_id": sys.argv[5],
}, ensure_ascii=False))
PY
)"
if [[ "$AUTHORIZE" -ne 1 && -f "$RECEIPT" ]]; then
  cp -a "$RECEIPT" "$RECEIPT.previous"
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
LOCK="${FAT_CN_LOCK:-/run/fat-cn-push.lock}"
script="$(cat "$HERE/cn_push_transaction.sh")"
payload="$(mktemp)"
trap 'rm -f "$payload"' EXIT
{
  printf '%s\n' "$HEADER"
  cat "$GZ"
} > "$payload"
remote_cmd="$(python3 - "$LOCK" "$script" "${FAT_CN_ROOT:-/opt/football-data}" "${FAT_CN_PAUSE_SEC:-}" "${FAT_CN_ACTIVATE_CMD:-}" "${FAT_CN_ROLLBACK_CMD:-}" <<'PY'
import shlex, sys
lock, script, root, pause, activate, rollback = sys.argv[1:]
env = " ".join([
    "FAT_CN_ROOT=" + shlex.quote(root),
    "FAT_CN_PAUSE_SEC=" + shlex.quote(pause),
    "FAT_CN_ACTIVATE_CMD=" + shlex.quote(activate),
    "FAT_CN_ROLLBACK_CMD=" + shlex.quote(rollback),
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
    FAT_CN_ACTIVATE_CMD="${FAT_CN_ACTIVATE_CMD:-}" \
    FAT_CN_ROLLBACK_CMD="${FAT_CN_ROLLBACK_CMD:-}" \
    bash "$HERE/remote_push_lock.sh" "$LOCK" \
    < "$payload" > "$DIR/commit.stdout"
  status=$?
fi
set -e
if [[ "$status" -ne 0 ]]; then
  cat "$DIR/commit.stdout" >&2 || true
  exit "$status"
fi
if [[ "$AUTHORIZE" -eq 1 ]]; then
  python3 - "$DIR/commit.stdout" "$DB_SHA" "$GZ_SHA" "$BASELINE" <<'PY'
import json, pathlib, sys
stdout, db, gz, baseline = sys.argv[1:]
line = ""
for raw in pathlib.Path(stdout).read_text(encoding="utf-8").splitlines():
    if raw.startswith("BASELINE_JSON "):
        line = raw[len("BASELINE_JSON "):]
if not line:
    sys.stderr.write("missing server baseline\n")
    sys.exit(5)
record = json.loads(line)
if record.get("db_sha256") != db or record.get("gz_sha256") != gz or not record.get("expected_commit_id"):
    sys.stderr.write("server baseline does not match this package\n")
    sys.exit(5)
path = pathlib.Path(baseline)
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.replace(path)
PY
  echo "BASELINE_AUTHORIZED"
  exit 0
fi
python3 - "$DIR/commit.stdout" "$DB_SHA" "$GZ_SHA" "$EXPECTED" "$RECEIPT" <<'PY'
import json, pathlib, sys
stdout, db, gz, expected, receipt = sys.argv[1:]
line = ""
for raw in pathlib.Path(stdout).read_text(encoding="utf-8").splitlines():
    if raw.startswith("COMMIT_JSON "):
        line = raw[len("COMMIT_JSON "):]
if not line:
    sys.stderr.write("missing server commit\n")
    sys.exit(5)
commit = json.loads(line)
if (
    commit.get("db_sha256") != db
    or commit.get("gz_sha256") != gz
    or commit.get("health_ok") is not True
    or commit.get("activated") is not True
    or commit.get("served_db_sha256") != db
    or commit.get("predecessor_commit_id") != expected
):
    sys.stderr.write("server commit does not match this package\n")
    sys.exit(5)
path = pathlib.Path(receipt)
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(json.dumps(commit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.replace(path)
PY
rm -f "$READY"
echo "PUSH_COMMITTED"
