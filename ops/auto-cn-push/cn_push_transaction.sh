#!/usr/bin/env bash
# 在已持有的 flock 下完成一次提交。调用方必须在整个进程期间持锁。
# 标准输入：第一行是 JSON，其余字节是本次 gz。
# 成功时向标准输出打印一行 COMMIT_JSON {...}。工作目录是唯一临时目录。
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
import gzip, hashlib, json, os, pathlib, shutil, sys, time, urllib.request
work = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
db_path = pathlib.Path(sys.argv[3])
header = json.loads((work / "header.json").read_text(encoding="utf-8"))
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
current = ""
if db_path.is_file():
    current = hashlib.sha256(db_path.read_bytes()).hexdigest()
pred = str(header.get("predecessor_db_sha256") or "").lower()
if current and current != db_sha and current != pred:
    sys.stderr.write(f"STALE_PREDECESSOR current={current}\n")
    sys.exit(12)
backup = ""
replaced = current != db_sha
if replaced:
    backup_dir = root / "backups" / f"db-{time.time_ns()}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if db_path.is_file():
        shutil.copy2(db_path, backup_dir / "app_latest.db")
        backup = str(backup_dir)
    incoming = work / "app_latest.db.incoming"
    incoming.write_bytes(raw)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(incoming, db_path)
health_cmd = os.environ.get("FAT_CN_HEALTH_CMD", "").strip()
health = None
try:
    if health_cmd:
        import subprocess
        health_raw = subprocess.check_output(health_cmd, shell=True, text=True)
    else:
        with urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=10) as resp:
            health_raw = resp.read().decode("utf-8")
    health = json.loads(health_raw)
except Exception:
    health = None
if not isinstance(health, dict) or health.get("ok") is not True:
    if backup:
        saved = pathlib.Path(backup) / "app_latest.db"
        if saved.is_file():
            os.replace(saved, db_path)
    elif replaced and db_path.is_file():
        db_path.unlink()
    sys.stderr.write("remote health failed\n")
    sys.exit(13)
commit_id = f"{time.time_ns()}"
commit = {
    "commit_id": commit_id,
    "jc_day": header.get("jc_day"),
    "db_sha256": db_sha,
    "gz_sha256": gz_sha,
    "predecessor_db_sha256": current,
    "health_ok": True,
    "backup": backup,
    "workdir": str(work),
}
commit_dir = root / "cn-push-commits"
commit_dir.mkdir(parents=True, exist_ok=True)
target = commit_dir / f"{commit_id}.json"
tmp = commit_dir / f".{commit_id}.tmp"
text = json.dumps(commit, ensure_ascii=False) + "\n"
tmp.write_text(text, encoding="utf-8")
os.replace(tmp, target)
latest = root / "LAST_COMMIT.json"
latest_tmp = root / ".LAST_COMMIT.json.tmp"
latest_tmp.write_text(text, encoding="utf-8")
os.replace(latest_tmp, latest)
sys.stdout.write("COMMIT_JSON " + json.dumps(commit, ensure_ascii=False) + "\n")
PY
