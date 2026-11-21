#!/usr/bin/env bash
# 维护迁移：把没有 source 字段的 MSI 原回执收成基线。
# 不改回执文件，不补写 source，也不手填 commit_id。
# 操作者必须显式声明证据来源，并给出原文件字节的 sha256。
set -euo pipefail
RECEIPT=""
SNAPSHOT=""
EVIDENCE=""
RECEIPT_SHA=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --receipt) RECEIPT="$2"; shift 2 ;;
    --snapshot) SNAPSHOT="$2"; shift 2 ;;
    --evidence-source) EVIDENCE="$2"; shift 2 ;;
    --receipt-sha256) RECEIPT_SHA="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
if [[ -z "$RECEIPT" || -z "$SNAPSHOT" || -z "$EVIDENCE" || -z "$RECEIPT_SHA" ]]; then
  echo "receipt, snapshot, evidence-source and receipt-sha256 are required" >&2
  exit 2
fi
ROOT="${FAT_CN_ROOT:?FAT_CN_ROOT required}"
DB="${FAT_CN_DB:-$ROOT/app_latest.db}"
python3 - "$RECEIPT" "$SNAPSHOT" "$ROOT" "$DB" "$EVIDENCE" "$RECEIPT_SHA" <<'PY'
import hashlib, json, os, pathlib, sys

receipt_path_s, snapshot_path_s, root_s, db_s, evidence, declared_sha = sys.argv[1:]
receipt_path = pathlib.Path(receipt_path_s)
root = pathlib.Path(root_s)
db_path = pathlib.Path(db_s)
declared_sha = declared_sha.strip().lower()
if evidence != "msi" or len(declared_sha) != 64:
    sys.stderr.write("EVIDENCE_SOURCE_REFUSED\n")
    sys.exit(19)
original = receipt_path.read_bytes()
file_sha = hashlib.sha256(original).hexdigest()
if file_sha != declared_sha:
    sys.stderr.write(f"RECEIPT_BYTES_MISMATCH file={file_sha} declared={declared_sha}\n")
    sys.exit(19)
receipt = json.loads(original.decode("utf-8-sig"))
if not isinstance(receipt, dict):
    sys.stderr.write("RECEIPT_FIELDS_MISSING\n")
    sys.exit(19)
if "source" in receipt:
    source = str(receipt.get("source") or "")
    if source == "msi":
        sys.stderr.write("USE_INIT_BASELINE\n")
    else:
        sys.stderr.write("NOT_MSI_RECEIPT\n")
    sys.exit(19)
db_sha = str(receipt.get("db_sha256") or "").lower()
gz_sha = str(receipt.get("gz_sha256") or "").lower()
jc_day = str(receipt.get("jc_day") or "")
if len(db_sha) != 64 or len(gz_sha) != 64 or not jc_day:
    sys.stderr.write("RECEIPT_FIELDS_MISSING\n")
    sys.exit(19)
snapshot = pathlib.Path(snapshot_path_s).read_bytes()
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
    "evidence_source": "msi",
    "receipt_had_source": False,
    "receipt_file_sha256": file_sha,
    "original_fields": sorted(receipt.keys()),
    "jc_day": jc_day,
    "db_sha256": db_sha,
    "gz_sha256": gz_sha,
    "snapshot_sha256": snap_sha,
    "remote_db_sha256": remote_sha,
    "initialized_from": "maintenance_baseline",
    "legacy_migration": True,
    "activated": False,
}
audit = {
    "kind": "legacy_msi_baseline_audit",
    "evidence_source": "msi",
    "receipt_had_source": False,
    "receipt_file_sha256": file_sha,
    "original_fields": sorted(receipt.keys()),
    "jc_day": jc_day,
    "db_sha256": db_sha,
    "gz_sha256": gz_sha,
    "snapshot_sha256": snap_sha,
    "remote_db_sha256": remote_sha,
    "commit_id": commit_id,
    "receipt_path_name": receipt_path.name,
}
latest = root / "LAST_COMMIT.json"
audit_path = root / "legacy_msi_baseline_audit.json"
if latest.is_file():
    existing = json.loads(latest.read_text(encoding="utf-8"))
    same = (
        existing.get("commit_id") == commit_id
        and existing.get("db_sha256") == db_sha
        and existing.get("gz_sha256") == gz_sha
        and existing.get("kind") == "baseline"
        and existing.get("receipt_source") == "msi"
        and existing.get("receipt_file_sha256") == file_sha
        and existing.get("receipt_had_source") is False
    )
    if not same:
        sys.stderr.write("BASELINE_EXISTS\n")
        sys.exit(19)
    sys.stdout.write("BASELINE_INITIALIZED " + commit_id + "\n")
    sys.exit(0)
root.mkdir(parents=True, exist_ok=True)

def publish(path: pathlib.Path, payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

publish(latest, record)
publish(audit_path, audit)
if receipt_path.read_bytes() != original:
    sys.stderr.write("RECEIPT_MUTATED\n")
    sys.exit(19)
sys.stdout.write("BASELINE_INITIALIZED " + commit_id + "\n")
PY
