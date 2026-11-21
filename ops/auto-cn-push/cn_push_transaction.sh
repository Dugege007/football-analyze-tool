#!/usr/bin/env bash
# 在已持有的 flock 下完成一次授权或提交。调用方必须在整个进程期间持锁。
# 标准输入：第一行是 JSON，其余字节是本次 gz。
# 授权成功打印 BASELINE_JSON。提交成功打印 COMMIT_JSON。工作目录是唯一临时目录。
set -euo pipefail
ROOT="${FAT_CN_ROOT:-/opt/football-data}"
DB="${FAT_CN_DB:-$ROOT/app_latest.db}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fat-cn-push.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
echo "WORKDIR=$WORK" >&2
IFS= read -r header
printf '%s\n' "$header" > "$WORK/header.json"
cat > "$WORK/app.db.gz"
if [[ -n "${FAT_CN_PAUSE_SEC:-}" ]]; then
  sleep "$FAT_CN_PAUSE_SEC"
fi
python3 - "$WORK" "$ROOT" "$DB" <<'PY'
import gzip, hashlib, json, os, pathlib, shutil, stat, struct, subprocess, sys, time

def gzip_commit_id(blob: bytes) -> str:
    if len(blob) < 10 or blob[0] != 0x1F or blob[1] != 0x8B:
        return ""
    flag = blob[3]
    pos = 10
    if flag & 4:
        if pos + 2 > len(blob):
            return ""
        extra = struct.unpack_from("<H", blob, pos)[0]
        pos += 2 + extra
    if flag & 8:
        end = blob.find(b"\0", pos)
        if end < 0:
            return ""
        pos = end + 1
    if flag & 16:
        end = blob.find(b"\0", pos)
        if end < 0:
            return ""
        return blob[pos:end].decode("ascii")
    return ""

work = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
db_path = pathlib.Path(sys.argv[3])
header = json.loads((work / "header.json").read_text(encoding="utf-8-sig"))
gz_bytes = (work / "app.db.gz").read_bytes()
gz_sha = hashlib.sha256(gz_bytes).hexdigest()
if gz_sha != str(header.get("gz_sha256") or "").lower():
    sys.stderr.write("gz hash mismatch\n")
    sys.exit(4)
try:
    raw = gzip.decompress(gz_bytes)
except OSError as exc:
    sys.stderr.write(f"gz decompress failed: {exc}\n")
    sys.exit(4)
db_sha = hashlib.sha256(raw).hexdigest()
if db_sha != str(header.get("db_sha256") or "").lower():
    sys.stderr.write("db hash mismatch\n")
    sys.exit(4)

def file_sha(path: pathlib.Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load_json(path: pathlib.Path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))

def atomic_write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)

last = load_json(root / "LAST_COMMIT.json")
if not isinstance(last, dict) or not last.get("commit_id") or not last.get("db_sha256"):
    sys.stderr.write("BASELINE_NOT_FROZEN\n")
    sys.exit(18)
current = file_sha(db_path)
if current != str(last.get("db_sha256") or "").lower():
    sys.stderr.write(f"STALE_BASELINE disk={current} commit={last.get('db_sha256')}\n")
    sys.exit(12)

auth_path = root / "authorized" / f"{gz_sha}.json"
stored = load_json(auth_path)
op = str(header.get("op") or "commit")
bound = gzip_commit_id(gz_bytes)
claimed = str(header.get("expected_commit_id") or "")
if not bound or claimed != bound:
    sys.stderr.write("PACKAGE_UNBOUND\n")
    sys.exit(18)
if bound != last["commit_id"] or str(last.get("db_sha256") or "").lower() != current:
    sys.stderr.write(f"STALE_BASELINE bound={bound} current={last['commit_id']}\n")
    sys.exit(12)

if op == "authorize":
    record = {
        "gz_sha256": gz_sha,
        "db_sha256": db_sha,
        "expected_commit_id": bound,
        "expected_db_sha256": current,
        "frozen": True,
    }
    if stored:
        if (
            stored.get("gz_sha256") != gz_sha
            or stored.get("db_sha256") != db_sha
            or stored.get("expected_commit_id") != bound
            or stored.get("expected_db_sha256") != current
        ):
            sys.stderr.write("BASELINE_FROZEN\n")
            sys.exit(17)
        record = stored
    else:
        atomic_write(auth_path, json.dumps(record, ensure_ascii=False) + "\n")
    sys.stdout.write("BASELINE_JSON " + json.dumps(record, ensure_ascii=False) + "\n")
    sys.exit(0)

if not stored:
    sys.stderr.write("BASELINE_NOT_FROZEN\n")
    sys.exit(18)
claimed = str(header.get("expected_commit_id") or "")
if (
    stored.get("gz_sha256") != gz_sha
    or stored.get("db_sha256") != db_sha
    or stored.get("expected_commit_id") != last["commit_id"]
    or stored.get("expected_db_sha256") != str(last["db_sha256"]).lower()
    or claimed != stored.get("expected_commit_id")
):
    sys.stderr.write(
        f"STALE_BASELINE stored={stored.get('expected_commit_id')} current={last['commit_id']} claimed={claimed}\n"
    )
    sys.exit(12)

activate_cmd = os.environ.get("FAT_CN_ACTIVATE_CMD", "").strip()
rollback_cmd = os.environ.get("FAT_CN_ROLLBACK_CMD", "").strip()
if not activate_cmd or not rollback_cmd:
    sys.stderr.write("ACTIVATE_HOOK_MISSING\n")
    sys.exit(14)

def publish(data: bytes, mode: int | None, owner) -> str:
    parent = db_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = parent / f".app_latest.db.{os.getpid()}.tmp"
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if mode is not None:
        os.chmod(tmp, mode)
    if owner is not None:
        try:
            os.chown(tmp, owner[0], owner[1])
        except PermissionError:
            tmp.unlink(missing_ok=True)
            sys.stderr.write("OWNER_INHERIT_FAILED\n")
            sys.exit(21)
    sys.stderr.write(f"DBTMP={tmp}\n")
    os.replace(tmp, db_path)
    return str(tmp)

def hook(role: str, expect: str) -> dict | None:
    cmd = activate_cmd if role == "activate" else rollback_cmd
    env = os.environ.copy()
    env["FAT_CN_DB"] = str(db_path)
    env["FAT_CN_HOOK_ROLE"] = role
    try:
        raw_out = subprocess.check_output(cmd, shell=True, text=True, env=env)
        proof = json.loads(raw_out)
    except Exception as exc:
        sys.stderr.write(f"hook {role} failed: {exc}\n")
        return None
    if not isinstance(proof, dict) or proof.get("ok") is not True or proof.get("read_only") is not True:
        return None
    if proof.get("fingerprint_source") != "running_api":
        sys.stderr.write("fingerprint is not from the running api\n")
        return None
    if str(proof.get("db_sha256") or "").lower() != expect:
        return proof
    return proof

previous = db_path.read_bytes()
previous_stat = db_path.stat()
mode = stat.S_IMODE(previous_stat.st_mode)
owner = (previous_stat.st_uid, previous_stat.st_gid)
backup_dir = root / "backups" / f"db-{time.time_ns()}"
backup_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2(db_path, backup_dir / "app_latest.db")
publish(raw, mode, owner)

def restore_file() -> None:
    publish(previous, mode, owner)

proof = hook("activate", db_sha)
if proof is None or str(proof.get("db_sha256") or "").lower() != db_sha:
    restore_file()
    restored = file_sha(db_path)
    back = hook("rollback", restored)
    if back is None or str(back.get("db_sha256") or "").lower() != restored or file_sha(db_path) != current:
        alert = {
            "severity": "critical",
            "ok": False,
            "reason": "service_rollback_unverified",
            "db_path": str(db_path),
            "expected_db_sha256": current,
            "disk_db_sha256": file_sha(db_path),
            "receipt_issued": False,
        }
        atomic_write(root / "CN_PUSH_ALERT.json", json.dumps(alert, ensure_ascii=False) + "\n")
        sys.stderr.write("CN_PUSH_ALERT service_rollback_unverified\n")
        sys.exit(16)
    sys.stderr.write("activate proof failed\n")
    sys.exit(13)

commit_id = str(time.time_ns())
commit = {
    "commit_id": commit_id,
    "jc_day": header.get("jc_day"),
    "db_sha256": db_sha,
    "gz_sha256": gz_sha,
    "predecessor_commit_id": bound,
    "expected_commit_id": bound,
    "served_db_sha256": db_sha,
    "read_only": True,
    "activated": True,
    "health_ok": True,
    "backup": str(backup_dir),
    "workdir": str(work),
}
text = json.dumps(commit, ensure_ascii=False) + "\n"
atomic_write(root / "cn-push-commits" / f"{commit_id}.json", text)
atomic_write(root / "LAST_COMMIT.json", text)
sys.stdout.write("COMMIT_JSON " + json.dumps(commit, ensure_ascii=False) + "\n")
PY
