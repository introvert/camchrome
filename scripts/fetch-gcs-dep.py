#!/usr/bin/env python3
"""Fetch one `dep_type: gcs` entry of a Chromium DEPS file, the way gclient would.

The lite source tarball leaves out GCS-hosted prebuilts (node, libclang, ...). DEPS pins
each object by name and sha256; this downloads the object(s) for that entry, checks the
hash and unpacks them where gclient would.

    scripts/fetch-gcs-dep.py build/src src/third_party/node/linux
    scripts/fetch-gcs-dep.py build/src src/third_party/llvm-libclang --match Linux_x64/
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def load_deps(path: Path) -> dict:
    scope: dict = {}
    env = {"Var": lambda name: scope["vars"][name], "Str": str}
    exec(compile(path.read_text(), str(path), "exec"), env, scope)  # noqa: S102 (trusted DEPS)
    return scope["deps"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="Chromium src directory (holds DEPS)")
    ap.add_argument("dep", help="DEPS key, e.g. src/third_party/node/linux")
    ap.add_argument("--match", default="", help="only objects whose name contains this")
    args = ap.parse_args()

    src = Path(args.src).resolve()
    deps = load_deps(src / "DEPS")
    entry = deps.get(args.dep)
    if not entry or entry.get("dep_type") != "gcs":
        print(f"{args.dep}: not a gcs dep in {src/'DEPS'}", file=sys.stderr)
        return 2
    out_dir = src.parent / args.dep  # DEPS keys start with "src/"
    out_dir.mkdir(parents=True, exist_ok=True)
    objects = [o for o in entry["objects"] if args.match in o["object_name"]]
    if not objects:
        print(f"{args.dep}: no object matches {args.match!r}", file=sys.stderr)
        return 2
    for obj in objects:
        url = f"https://storage.googleapis.com/{entry['bucket']}/{obj['object_name']}"
        with tempfile.NamedTemporaryFile(suffix=".tar") as tmp:
            print(f"fetching {url}")
            with urllib.request.urlopen(url) as r:
                data = r.read()
            digest = hashlib.sha256(data).hexdigest()
            if digest != obj["sha256sum"]:
                print(f"sha256 mismatch for {url}: {digest} != {obj['sha256sum']}", file=sys.stderr)
                return 1
            output_file = obj.get("output_file", "")
            if output_file and not output_file.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar")):
                # A single file (e.g. buildtools/linux64-format/clang-format), not an archive.
                # Archives named by output_file (node-linux-x64.tar.gz) are unpacked below, as
                # gclient does.
                dest = out_dir / obj["output_file"]
                dest.write_bytes(data)
                dest.chmod(0o755)
                print(f"  ok: sha256 {digest[:16]}..., written to {dest}")
                continue
            tmp.write(data)
            tmp.flush()
            with tarfile.open(tmp.name) as tar:
                tar.extractall(out_dir, filter="data")
        print(f"  ok: sha256 {digest[:16]}..., unpacked into {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
