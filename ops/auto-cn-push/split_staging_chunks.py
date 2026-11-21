#!/usr/bin/env python3
"""Split staging app_latest.db.gz into ≤chunk_bytes parts for CopyFromBox."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

OPS = Path(os.environ.get("AUTO_CN_PUSH_HOME", Path(__file__).resolve().parent))
DEFAULT_STAGING = OPS / "staging"
DEFAULT_OUT = OPS / "chunks-deliver"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", type=Path, default=DEFAULT_STAGING)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--chunk-bytes", type=int, default=256 * 1024)
    args = ap.parse_args(argv)

    gz = args.staging / "app_latest.db.gz"
    meta = args.staging / "meta.json"
    gz_sha = args.staging / "app_latest.db.gz.sha256"
    db_sha = args.staging / "app_latest.db.sha256"
    for p in (gz, meta, gz_sha, db_sha):
        if not p.is_file():
            raise SystemExit(f"missing {p}")

    meta_obj = json.loads(meta.read_text(encoding="utf-8"))
    pending_path = Path(os.environ.get("AUTO_CN_PUSH_HOME", str(OPS))) / "markers" / "pending_push.json"
    if pending_path.is_file():
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        want_gz = (pending.get("gz_sha256") or "").lower()
        want_db = (pending.get("db_sha256") or "").lower()
        got_gz = str(meta_obj.get("gz_sha256") or "").lower()
        got_db = str(meta_obj.get("db_sha256") or "").lower()
        if (want_gz and got_gz != want_gz) or (want_db and got_db != want_db):
            raise SystemExit("staging does not match pending; refuse to split an old package")

    if args.out_dir.exists():
        shutil.rmtree(args.out_dir)
    args.out_dir.mkdir(parents=True)

    parts = []
    with gz.open("rb") as fin:
        idx = 0
        while True:
            blob = fin.read(args.chunk_bytes)
            if not blob:
                break
            name = f"p{idx:02d}"
            (args.out_dir / name).write_bytes(blob)
            parts.append(name)
            idx += 1

    for name in ("meta.json", "app_latest.db.gz.sha256", "app_latest.db.sha256"):
        shutil.copy2(args.staging / name, args.out_dir / name)

    manifest = {
        "jc_day": meta_obj.get("jc_day"),
        "gz_sha256": meta_obj.get("gz_sha256") or gz_sha.read_text(encoding="ascii").split()[0].strip(),
        "db_sha256": meta_obj.get("db_sha256") or db_sha.read_text(encoding="ascii").split()[0].strip(),
        "gz_bytes": gz.stat().st_size,
        "chunk_bytes": args.chunk_bytes,
        "n": len(parts),
        "parts": parts,
        "note": "Copy parts + sidecars to YPC incoming; run install_package_atomic.ps1 -ChunksDir ...",
    }
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
