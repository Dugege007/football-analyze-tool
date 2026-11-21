#!/usr/bin/env python3
"""Group chunks-deliver parts into ~8MiB tars so CopyFromBox is a handful of files."""
from __future__ import annotations

import argparse
import hashlib
import tarfile
from pathlib import Path

OPS = Path(__file__).resolve().parent
SIDECARS = ("manifest.json", "meta.json", "app_latest.db.gz.sha256", "app_latest.db.sha256")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks-dir", type=Path, default=OPS / "chunks-deliver")
    ap.add_argument("--out-dir", type=Path, default=OPS / "chunks-deliver-packs")
    ap.add_argument("--group-size", type=int, default=32)
    args = ap.parse_args(argv)

    src = args.chunks_dir
    out = args.out_dir
    if not (src / "manifest.json").is_file():
        raise SystemExit(f"missing {src / 'manifest.json'}")
    if out.exists():
        for old in out.iterdir():
            if old.is_file():
                old.unlink()
    out.mkdir(parents=True, exist_ok=True)

    parts = sorted(
        [p.name for p in src.iterdir() if p.is_file() and p.name.startswith("p") and p.name[1:].isdigit()],
        key=lambda n: int(n[1:]),
    )
    if not parts:
        raise SystemExit(f"no parts in {src}")

    written: list[str] = []
    gs = args.group_size
    for i in range(0, len(parts), gs):
        group = parts[i : i + gs]
        first = int(group[0][1:])
        last = int(group[-1][1:])
        name = f"pack-{first:03d}-{last:03d}.tar"
        dest = out / name
        with tarfile.open(dest, "w") as tf:
            for part in group:
                tf.add(src / part, arcname=part)
        written.append(name)

    side = out / "pack-sidecar.tar"
    with tarfile.open(side, "w") as tf:
        for name in SIDECARS:
            p = src / name
            if not p.is_file():
                raise SystemExit(f"missing sidecar {p}")
            tf.add(p, arcname=name)
    written.append(side.name)

    sums = out / "SHA256SUMS"
    lines = []
    for name in written:
        digest = sha256_file(out / name)
        lines.append(f"{digest}  {name}\n")
    sums.write_text("".join(lines), encoding="ascii")
    print(f"packs={len(written)} parts={len(parts)} out={out}")
    for line in lines:
        print(line.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
