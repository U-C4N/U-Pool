"""Package U-Pool into a standalone Windows folder.

Usage:  python scripts/build.py [--skip-ui] [--clean]

Produces ``dist/U-Pool/U-Pool.exe`` using PyInstaller in *onedir* mode. Onefile
is deliberately avoided: it unpacks the whole bundle into a temp directory on
every launch, which costs about a second of startup - the exact thing this app
is supposed to be good at.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
        # Keep the bundle small: none of these are used at runtime.
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "unittest",
        "--exclude-module",
        "pydoc",
        str(ROOT / "src" / "upool" / "__main__.py"),
    ]
    run(command, cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the U-Pool desktop bundle.")
    parser.add_argument("--skip-ui", action="store_true", help="reuse the existing ui/out export")
    parser.add_argument("--clean", action="store_true", help="remove build/ and dist/ first")
    args = parser.parse_args()

    if args.clean:
        for path in (BUILD, DIST):
            shutil.rmtree(path, ignore_errors=True)

    if args.skip_ui:
        if not (UI_OUT / "index.html").exists():
            raise SystemExit("--skip-ui was passed but ui/out is empty. Run without it once.")
    else:
        build_ui()

    build_app()
    print(f"\nDone. Bundle: {DIST / 'U-Pool'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
