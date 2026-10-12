"""Package U-Pool into a standalone Windows folder.

Usage:  python scripts/build.py [--skip-ui] [--clean]

Produces ``dist/U-Pool/U-Pool.exe`` using PyInstaller in *onedir* mode. Onefile
is deliberately avoided: it unpacks the whole bundle into a temp directory on
every launch, which costs about a second of startup - the exact thing this app
is supposed to be good at.

It also zips the bundle as ``dist/U-Pool-<version>-win64.zip`` and prints its
SHA-256. That archive is what the in-app updater downloads, so it has to be
attached to the GitHub release as an asset - and the name has to keep matching
``upool.release.ASSET_RE``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from upool import __version__  # noqa: E402 - needs the path above
UI_DIR = ROOT / "ui"
UI_OUT = UI_DIR / "out"
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def run(command: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=cwd, shell=(sys.platform == "win32"))
    if result.returncode != 0:
        raise SystemExit(f"Command failed with exit code {result.returncode}: {' '.join(command)}")


def build_ui() -> None:
    if not (UI_DIR / "node_modules").exists():
        run(["npm", "install", "--no-audit", "--no-fund"], cwd=UI_DIR)
    run(["npm", "run", "build"], cwd=UI_DIR)
    if not (UI_OUT / "index.html").exists():
        raise SystemExit(f"Expected the static export at {UI_OUT}; nothing was produced.")


def build_app() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("PyInstaller is missing. Install it with: pip install -e .[dev]") from None

    # PyInstaller wants os.pathsep between source and destination.
    separator = ";" if sys.platform == "win32" else ":"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "U-Pool",
        "--paths",
        str(ROOT / "src"),
        "--add-data",
        f"{UI_OUT}{separator}ui/out",
        "--add-data",
        f"{BUILD / 'proxy-runtimes'}{separator}proxy-runtimes",
        "--add-data",
        f"{ROOT / 'THIRD_PARTY_LICENSES.txt'}{separator}.",
        # PyYAML is reached only through ``upool.yamlio``, which the Hermes adapter
        # imports. Naming it means a missing bundle is a build failure rather than
        # something the first Hermes switch discovers.
        "--hidden-import",
        "yaml",
        # Keep the bundle small: none of these are used at runtime.
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "unittest",
        "--exclude-module",
        "pydoc",
    ]
    if sys.platform == "win32":
        # The Windows shell uses WinForms/WebView2. Installed development Qt
        # packages must not change the release's dependencies or GUI selection.
        for module in ("PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy"):
            command.extend(["--exclude-module", module])
    command.append(str(ROOT / "src" / "upool" / "__main__.py"))
    run(command, cwd=ROOT)


def collect_licenses() -> None:
    """Keep original notices for the desktop's bundled Python dependencies."""
    destination = DIST / "U-Pool" / "_internal" / "dependency-licenses"
    destination.mkdir(parents=True, exist_ok=True)
    dependencies = ("pywebview", "tomlkit", "PyYAML", "pythonnet", "clr_loader", "cffi",
                    "pycparser", "proxy_tools", "bottle", "typing_extensions", "setuptools", "packaging", "pyinstaller")
    provenance = []
    for name in dependencies:
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        provenance.append(f"{name} {distribution.version}")
        for index, relative in enumerate(distribution.files or []):
            if not relative.name.lower().startswith(("license", "copying", "authors")):
                continue
            source = Path(distribution.locate_file(relative))
            if source.is_file():
                shutil.copyfile(source, destination / f"{name}-{index}-{source.name}")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, destination / "CPython-LICENSE.txt")
    (destination / "BUNDLED-DEPENDENCIES.txt").write_text("\n".join(provenance) + "\n", encoding="utf-8")


def smoke_test() -> None:
    """Start the built exe once before anyone downloads it.

    ``--version`` exits before the webview is touched, so this is a cheap check
    that the bundle imports at all - which is exactly the failure a relative
    import in the entry point produces, and it is invisible until launch.
    """
    exe = DIST / "U-Pool" / ("U-Pool.exe" if sys.platform == "win32" else "U-Pool")
    result = subprocess.run([str(exe), "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"The bundle does not start: {exe} --version exited {result.returncode}.\n"
            f"{result.stdout}{result.stderr}"
        )
    print(f"Smoke test: {exe.name} --version exited cleanly.")


def package() -> Path:
    """Zip the bundle for the GitHub release, and print the checksum to publish."""
    base = DIST / f"U-Pool-{__version__}-win64"
    archive = Path(shutil.make_archive(str(base), "zip", root_dir=DIST, base_dir="U-Pool"))
    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    checksums = DIST / "SHA256SUMS.txt"
    checksums.write_text(f"{digest.hexdigest()}  {archive.name}\n", encoding="utf-8")
    size_mb = archive.stat().st_size / 1024 / 1024
    print(f"\nArchive: {archive}  ({size_mb:.1f} MB)")
    print(f"SHA-256: {digest.hexdigest()}")
    print(f"Wrote:   {checksums}")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the U-Pool desktop bundle.")
    parser.add_argument("--skip-ui", action="store_true", help="reuse the existing ui/out export")
    parser.add_argument("--clean", action="store_true", help="remove build/ and dist/ first")
    parser.add_argument("--reuse-proxy-worker", action="store_true", help="reuse the checksum-verified built API worker")
    args = parser.parse_args()

    if args.clean:
        for path in (BUILD, DIST):
            resolved = path.resolve()
            if resolved.parent != ROOT.resolve() or resolved.name not in {"build", "dist"}:
                raise SystemExit("Refusing to clean a path outside the project build directories.")
            shutil.rmtree(resolved, ignore_errors=True)

    if args.skip_ui:
        if not (UI_OUT / "index.html").exists():
            raise SystemExit("--skip-ui was passed but ui/out is empty. Run without it once.")
    else:
        build_ui()

    prepare = [sys.executable, str(ROOT / "scripts" / "prepare_proxy_runtimes.py")]
    if args.reuse_proxy_worker:
        prepare.append("--reuse-worker")
    run(prepare, cwd=ROOT)
    build_app()
    smoke_test()
    collect_licenses()
    archive = package()
    print(f"\nDone. Bundle: {DIST / 'U-Pool'}")
    print(f"Attach {archive.name} and SHA256SUMS.txt to the release, or the updater has")
    print("nothing to download.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
