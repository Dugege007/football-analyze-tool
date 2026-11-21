#!/usr/bin/env bash
# 维护迁移：把没有 source 字段的原回执收成基线。
# evidence-source 只是操作者声明，不是机器认证。
# 不改回执文件，不补写 source，也不手填 commit_id。
# 与正式推送共用 FAT_CN_LOCK。锁内重读证据；先落审计，基线是最后的提交点。
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
LOCK="${FAT_CN_LOCK:-/run/fat-cn-push.lock}"
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "REMOTE_PUSH_LOCK_HELD" >&2
  exit 9
fi
python3 - "$RECEIPT" "$SNAPSHOT" "$ROOT" "$DB" "$EVIDENCE" "$RECEIPT_SHA" <<'PY'
import hashlib, json, os, pathlib, sys, time

receipt_path_s, snapshot_path_s, root_s, db_s, evidence, declared_sha = sys.argv[1:]
receipt_path = pathlib.Path(receipt_path_s)
snapshot_path = pathlib.Path(snapshot_path_s)
root = pathlib.Path(root_s)
db_path = pathlib.Path(db_s)
declared_sha = declared_sha.strip().lower()
fault = os.environ.get("FAT_CN_MIGRATE_FAULT", "").strip()
pause = float(os.environ.get("FAT_CN_MIGRATE_PAUSE_SEC", "0") or "0")


def fail(token: str) -> None:
    sys.stderr.write(token + "\n")
    raise SystemExit(19)


_MISSING = object()


def load_json(path: pathlib.Path):
    """只有文件真正不存在才返回缺失标记。JSON null 不是缺失。"""
    if not path.is_file():
        return _MISSING
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        fail("BASELINE_CORRUPT" if path.name == "LAST_COMMIT.json" else "AUDIT_CORRUPT")
    if not isinstance(value, dict):
        fail("BASELINE_NOT_OBJECT" if path.name == "LAST_COMMIT.json" else "AUDIT_NOT_OBJECT")
    return value


def fsync_dir(path: pathlib.Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path: pathlib.Path, payload: dict, fault_name: str) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if fault == fault_name:
        tmp.unlink(missing_ok=True)
        fail("AUDIT_WRITE_FAILED" if fault_name.startswith("audit") else "BASELINE_WRITE_FAILED")
    os.replace(tmp, path)
    fsync_dir(path.parent)
    got = path.read_text(encoding="utf-8")
    if got != text or json.loads(got) != payload:
        fail("AUDIT_VERIFY_FAILED" if path.name.startswith("legacy_") else "BASELINE_VERIFY_FAILED")


def read_evidence() -> dict:
    if evidence != "msi" or len(declared_sha) != 64:
        fail("EVIDENCE_SOURCE_REFUSED")
    original = receipt_path.read_bytes()
    file_sha = hashlib.sha256(original).hexdigest()
    if file_sha != declared_sha:
        fail(f"RECEIPT_BYTES_MISMATCH file={file_sha} declared={declared_sha}")
    receipt = json.loads(original.decode("utf-8-sig"))
    if not isinstance(receipt, dict):
        fail("RECEIPT_FIELDS_MISSING")
    if "source" in receipt:
        source = str(receipt.get("source") or "")
        fail("USE_INIT_BASELINE" if source == "msi" else "NOT_MSI_RECEIPT")
    db_sha = str(receipt.get("db_sha256") or "").lower()
    gz_sha = str(receipt.get("gz_sha256") or "").lower()
    jc_day = str(receipt.get("jc_day") or "")
    if len(db_sha) != 64 or len(gz_sha) != 64 or not jc_day:
        fail("RECEIPT_FIELDS_MISSING")
    snapshot = snapshot_path.read_bytes()
    snap_sha = hashlib.sha256(snapshot).hexdigest()
    if not db_path.is_file():
        fail("REMOTE_DB_MISSING")
    remote_sha = hashlib.sha256(db_path.read_bytes()).hexdigest()
    if not (snap_sha == db_sha == remote_sha):
        fail(f"BASELINE_HASH_MISMATCH snapshot={snap_sha} receipt={db_sha} remote={remote_sha}")
    identity = f"msi\n{jc_day}\n{db_sha}\n{gz_sha}".encode()
    commit_id = "baseline-" + hashlib.sha256(identity).hexdigest()
    record = {
        "activated": False,
        "commit_id": commit_id,
        "db_sha256": db_sha,
        "evidence_source": "msi",
        "gz_sha256": gz_sha,
        "initialized_from": "maintenance_baseline",
        "jc_day": jc_day,
        "kind": "baseline",
        "legacy_migration": True,
        "original_fields": sorted(receipt.keys()),
        "receipt_file_sha256": file_sha,
        "receipt_had_source": False,
        "receipt_source": "msi",
        "remote_db_sha256": remote_sha,
        "snapshot_sha256": snap_sha,
        "source": "msi_receipt",
    }
    audit = {
        "commit_id": commit_id,
        "db_sha256": db_sha,
        "evidence_source": "msi",
        "gz_sha256": gz_sha,
        "jc_day": jc_day,
        "kind": "legacy_msi_baseline_audit",
        "original_fields": sorted(receipt.keys()),
        "receipt_file_sha256": file_sha,
        "receipt_had_source": False,
        "receipt_path_name": receipt_path.name,
        "remote_db_sha256": remote_sha,
        "snapshot_sha256": snap_sha,
    }
    return {"original": original, "record": record, "audit": audit}


def same_record(existing: dict | None, expected: dict, keys: list[str]) -> bool:
    if not isinstance(existing, dict):
        return False
    return all(existing.get(key) == expected[key] for key in keys)


try:
    root.mkdir(parents=True, exist_ok=True)
    fsync_dir(root)
    evidence_now = read_evidence()
    if pause:
        time.sleep(pause)
    # 锁内重查。暂停期间正式提交可能已经推进。
    evidence_now = read_evidence()
    record = evidence_now["record"]
    audit = evidence_now["audit"]
    original = evidence_now["original"]
    latest = root / "LAST_COMMIT.json"
    audit_path = root / "legacy_msi_baseline_audit.json"
    existing = load_json(latest)
    existing_audit = load_json(audit_path)
    baseline_keys = [
        "commit_id", "db_sha256", "gz_sha256", "kind", "receipt_source",
        "receipt_file_sha256", "receipt_had_source", "legacy_migration", "evidence_source",
    ]
    audit_keys = list(audit.keys())
    if existing is not _MISSING and not same_record(existing, record, baseline_keys):
        fail("BASELINE_EXISTS")
    if existing_audit is not _MISSING and not same_record(existing_audit, audit, audit_keys):
        fail("AUDIT_CONFLICT")
    if existing_audit is _MISSING:
        publish(audit_path, audit, "audit_replace")
    elif fault == "audit_replace":
        fail("AUDIT_WRITE_FAILED")
    if fault == "before_baseline":
        fail("BASELINE_NOT_PUBLISHED")
    confirmed = read_evidence()
    if confirmed["record"] != record or confirmed["audit"] != audit or confirmed["original"] != original:
        fail("EVIDENCE_CHANGED")
    current = load_json(latest)
    if current is not _MISSING and not same_record(current, record, baseline_keys):
        fail("BASELINE_EXISTS")
    if current is _MISSING:
        publish(latest, record, "baseline_replace")
    if receipt_path.read_bytes() != original:
        fail("RECEIPT_MUTATED")
    verified_audit = load_json(audit_path)
    verified_base = load_json(latest)
    if not same_record(verified_audit, audit, audit_keys) or not same_record(verified_base, record, baseline_keys):
        fail("BASELINE_VERIFY_FAILED")
    sys.stdout.write("BASELINE_INITIALIZED " + record["commit_id"] + "\n")
except SystemExit:
    raise
except Exception as exc:
    sys.stderr.write(f"MIGRATE_FAILED {exc}\n")
    raise SystemExit(19)
PY
