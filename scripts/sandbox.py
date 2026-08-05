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
    (home / ".hermes").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "opencode").mkdir(parents=True, exist_ok=True)

    from upool import paths, winenv

    winenv.ENV_KEY = SCRATCH_ENV_KEY

    # Hermes, OpenCode and Cursor do not live under the home directory, so the
    # redirect above is not enough on its own. ``paths.sandboxed`` is what makes
    # their helpers ignore %LOCALAPPDATA%, HERMES_HOME, OPENCODE_CONFIG and
    # %APPDATA%; this checks it actually did, because getting it wrong writes to
    # the developer's live Hermes config - or worse, to the state.vscdb their
    # running editor is holding open - rather than to a temp directory.
    for resolved in (
        paths.claude_settings_file(),
        paths.codex_config_file(),
        paths.hermes_config_file(),
        paths.opencode_config_file(),
        paths.cursor_state_db(),
    ):
        if not str(resolved).startswith(str(home)):
            raise SystemExit(f"[sandbox] {resolved} escaped the sandbox - refusing to run")

    # After the check, never before: this is the one directory the sandbox creates
    # whose real counterpart already exists, so a mkdir on an unredirected path
    # would succeed silently instead of tripping the guard above.
    paths.cursor_state_db().parent.mkdir(parents=True, exist_ok=True)


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
