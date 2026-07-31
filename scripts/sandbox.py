"""Run U-Pool code against a throwaway home instead of the real one.

Every write path in this project targets a file some other program is using right
now - ``~/.claude/settings.json``, ``~/.codex/config.toml``, ``HKCU\\Environment``.
A one-off script that imports :mod:`upool` and calls ``apply()`` to check a
hypothesis therefore reconfigures the machine it is being checked on. That has
happened: a script probing whether a provider named ``Path`` could clobber the
real ``PATH`` wrote a placeholder base URL into a live settings.json, and every
Anthropic request on the machine started failing DNS.

So verification of a write path goes through here:

    python scripts/sandbox.py -c "from upool import adapters; ..."
    python scripts/sandbox.py my_probe.py
    python scripts/sandbox.py            # interactive, upool already imported

The sandbox is a fresh temp directory per run, printed on the way in and left
behind for inspection. ``UPOOL_FAKE_HOME`` and ``UPOOL_HOME`` both point into it,
so ``paths.home()`` and ``paths.app_home()`` resolve there and nothing under the
real home is reachable.

The registry is the one thing this cannot redirect - ``winenv.ENV_KEY`` is a
module constant, patched by the test suite - so it is patched here too, onto the
same scratch key the suite uses.
"""

from __future__ import annotations

import runpy
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

# The suite's key, deliberately: two sandboxes fighting over one scratch key is
# better than either of them touching HKCU\Environment.
SCRATCH_ENV_KEY = r"Software\U-Pool-Tests\Environment"


def _prepare(home: Path) -> None:
    """Point every path helper and the registry key at ``home``."""
    import os

    os.environ["UPOOL_FAKE_HOME"] = str(home)
    os.environ["UPOOL_HOME"] = str(home / ".u-pool")
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".codex").mkdir(parents=True, exist_ok=True)

    from upool import winenv

    winenv.ENV_KEY = SCRATCH_ENV_KEY


def main(argv: list[str]) -> int:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    home = Path(tempfile.mkdtemp(prefix="upool-sandbox-"))
    _prepare(home)
    print(f"[sandbox] home = {home}", file=sys.stderr)
    print(f"[sandbox] registry = HKCU\\{SCRATCH_ENV_KEY}", file=sys.stderr)

    if not argv:
        import code

        import upool  # noqa: F401 - handed to the caller's namespace

        code.interact(local={"upool": upool, "sandbox_home": home})
        return 0

    if argv[0] == "-c":
        if len(argv) < 2:
            print("sandbox.py -c needs a statement to run", file=sys.stderr)
            return 2
        exec(argv[1], {"__name__": "__main__", "sandbox_home": home})  # noqa: S102
        return 0

    script = Path(argv[0])
    if not script.is_file():
        print(f"no such script: {script}", file=sys.stderr)
        return 2
    sys.argv = [str(script), *argv[1:]]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    # Run in-process rather than re-exec'd: the caller wants the traceback from
    # their own code, not from a wrapper.
    raise SystemExit(main(sys.argv[1:]))
