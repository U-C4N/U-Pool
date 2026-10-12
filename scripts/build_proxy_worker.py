"""Build the isolated Windows worker from a checksum-pinned upstream source.

This is a release/build action, not an end-user installation requirement.
Produces build/proxy-runtimes/chatgpt-worker.zip and a manifest containing
the actual archive SHA-256. Never writes into a user's installed U-Pool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "proxy-worker"
OUTPUT = ROOT / "build" / "proxy-runtimes"
REQUIREMENTS = ["PyInstaller==6.22.0", "websockets==15.0.1", "portalocker==3.2.0"]


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, help="reuse an already-downloaded pinned source ZIP")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise SystemExit("Build the Windows amd64 worker using 64-bit Python on Windows.")
    manifest = json.loads((ROOT / "runtime-manifest.json").read_text(encoding="utf-8"))
    upstream = manifest["worker"]
    BUILD.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    archive = BUILD / "upstream.zip"
    if args.source_archive:
        shutil.copyfile(args.source_archive, archive)
    elif not archive.exists() or digest(archive) != upstream["source_sha256"]:
        with urllib.request.urlopen(upstream["source_url"], timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
    if digest(archive) != upstream["source_sha256"]:
        raise SystemExit("Pinned ChatGPT-Web2API source checksum verification failed.")
    source = BUILD / "source"
    source.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        root = source.resolve()
        for item in package.infolist():
            if not (root / item.filename).resolve().is_relative_to(root):
                raise SystemExit("Unsafe upstream archive path")
        package.extractall(source)
    package_root = source / ("ChatGPT-Web2API-" + upstream["source_commit"])
    environment = BUILD / "venv"
    python = environment / "Scripts" / "python.exe"
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    run([str(python), "-m", "pip", "install", "--disable-pip-version-check", *REQUIREMENTS])
    # Only the browser driver is imported. No MCP/aiohttp server is shipped.
    run([str(python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
         "--name", "upool-chatgpt-worker", "--distpath", str(BUILD / "dist"),
         "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
         "--paths", str(package_root / "src"), "--collect-submodules", "chatgpt_web2api",
         "--exclude-module", "chatgpt_web2api.mcp_server", "--exclude-module", "chatgpt_web2api.api_server",
         "--exclude-module", "chatgpt_web2api.service", "--exclude-module", "tkinter",
         str(ROOT / "src" / "upool" / "proxy" / "worker.py")])
    dist = BUILD / "dist" / "upool-chatgpt-worker"
    run([str(dist / "upool-chatgpt-worker.exe"), "--version"])
    run([str(dist / "upool-chatgpt-worker.exe"), "--self-test"])
    shutil.copyfile(package_root / "LICENSE", dist / "ChatGPT-Web2API-LICENSE.txt")
    # Capture installed dependency notices alongside the compiled worker.
    libraries = environment / "Lib" / "site-packages"
    notices = dist / "dependency-licenses"
    notices.mkdir(exist_ok=True)
    for name in ("websockets", "portalocker", "pywin32", "pyinstaller"):
        for metadata in libraries.glob(name + "-*.dist-info"):
            for candidate in metadata.rglob("*"):
                if candidate.is_file() and ("license" in candidate.name.lower() or "copying" in candidate.name.lower()):
                    shutil.copyfile(candidate, notices / (name + "-" + candidate.name))
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, notices / "Python-LICENSE.txt")
    target = Path(shutil.make_archive(str(OUTPUT / "chatgpt-worker"), "zip", root_dir=BUILD / "dist", base_dir=dist.name))
    manifest["worker"].update({"sha256": digest(target), "bundle": target.name})
    (OUTPUT / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Worker: {target}\nSHA-256: {manifest['worker']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
