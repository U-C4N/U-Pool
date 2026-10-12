"""Prepare version-pinned API assets for a desktop release.

The small worker is bundled. CLIProxyAPI and Chrome remain first-use downloads
with fixed hashes in the same manifest; end users need no Python installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "proxy-runtimes"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse-worker", action="store_true", help="reuse a verified previously-built worker")
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()
    if not args.reuse_worker:
        command = [sys.executable, str(ROOT / "scripts" / "build_proxy_worker.py")]
        if args.source_archive:
            command.extend(["--source-archive", str(args.source_archive)])
        subprocess.run(command, cwd=ROOT, check=True)
    manifest = json.loads((OUTPUT / "runtime-manifest.json").read_text(encoding="utf-8"))
    archive = OUTPUT / manifest["worker"]["bundle"]
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    if sha != manifest["worker"]["sha256"]:
        raise SystemExit("Built worker archive does not match its manifest.")
    print(f"Verified pinned API assets in {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
