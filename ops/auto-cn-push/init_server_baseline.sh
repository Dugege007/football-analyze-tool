#!/usr/bin/env bash
# 维护动作：用 MSI 原回执、对应快照和服务器上的库文件生成基线。
# 三者的 db 哈希必须一致。不会把当时远端的库哈希填进推送前驱，也不写 YuePC 回执。
set -euo pipefail
RECEIPT=""
SNAPSHOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --receipt) RECEIPT="$2"; shift 2 ;;
    --snapshot) SNAPSHOT="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
if [[ -z "$RECEIPT" || -z "$SNAPSHOT" ]]; then
  echo "receipt and snapshot are required" >&2
  exit 2
fi
ROOT="${FAT_CN_ROOT:?FAT_CN_ROOT required}"
DB="${FAT_CN_DB:-$ROOT/app_latest.db}"
python3 - "$RECEIPT" "$SNAPSHOT" "$ROOT" "$DB" <<'PY'
import hashlib, json, os, pathlib, sys

receipt_path, snapshot_path, root_s, db_s = sys.argv[1:]
root = pathlib.Path(root_s)
db_path = pathlib.Path(db_s)
receipt = json.loads(pathlib.Path(receipt_path).read_text(encoding="utf-8-sig"))
source = str(receipt.get("source") or "")
if source != "msi":
    sys.stderr.write("NOT_MSI_RECEIPT\n")
    sys.exit(19)
db_sha = str(receipt.get("db_sha256") or "").lower()
gz_sha = str(receipt.get("gz_sha256") or "").lower()
jc_day = str(receipt.get("jc_day") or "")
if len(db_sha) != 64 or len(gz_sha) != 64 or not jc_day:
    sys.stderr.write("RECEIPT_FIELDS_MISSING\n")
    sys.exit(19)
snapshot = pathlib.Path(snapshot_path).read_bytes()
snap_sha = hashlib.sha256(snapshot).hexdigest()
if not db_path.is_file():
    sys.stderr.write("REMOTE_DB_MISSING\n")
    sys.exit(19)
remote_sha = hashlib.sha256(db_path.read_bytes()).hexdigest()
if not (snap_sha == db_sha == remote_sha):
    sys.stderr.write(f"BASELINE_HASH_MISMATCH snapshot={snap_sha} receipt={db_sha} remote={remote_sha}\n")
    sys.exit(19)
identity = f"msi\n{jc_day}\n{db_sha}\n{gz_sha}".encode()
commit_id = "baseline-" + hashlib.sha256(identity).hexdigest()
record = {
    "commit_id": commit_id,
    "kind": "baseline",
    "source": "msi_receipt",
    "receipt_source": "msi",
    "jc_day": jc_day,
    "db_sha256": db_sha,
    "gz_sha256": gz_sha,
    "snapshot_sha256": snap_sha,
    "remote_db_sha256": remote_sha,
    "initialized_from": "maintenance_baseline",
    "activated": False,
}
latest = root / "LAST_COMMIT.json"
if latest.is_file():
    existing = json.loads(latest.read_text(encoding="utf-8"))
    same = (
        existing.get("commit_id") == commit_id
        and existing.get("db_sha256") == db_sha
        and existing.get("gz_sha256") == gz_sha
        and existing.get("kind") == "baseline"
        and existing.get("receipt_source") == "msi"
    )
    if not same:
        sys.stderr.write("BASELINE_EXISTS\n")
        sys.exit(19)
    sys.stdout.write("BASELINE_INITIALIZED " + commit_id + "\n")
    sys.exit(0)
root.mkdir(parents=True, exist_ok=True)
text = json.dumps(record, ensure_ascii=False) + "\n"
tmp = root / f".LAST_COMMIT.json.{os.getpid()}.tmp"
tmp.write_text(text, encoding="utf-8")
os.replace(tmp, latest)
sys.stdout.write("BASELINE_INITIALIZED " + commit_id + "\n")
PY
