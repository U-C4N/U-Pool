"""Which version of the target CLIs is actually installed on this machine.

U-Pool has always shown its own version and never theirs, which is the wrong way
round: the question a provider switch raises is whether the CLI reading the file
is new enough to understand it.

Finding the executable is most of the work. ``shutil.which`` is right when it
works, but U-Pool runs as a windowed process launched from Explorer or from the
sign-in entry, and that process inherits the environment as it stood at logon -
not the one a terminal has after the user's profile scripts have run. On the
machine this was written for, ``claude.cmd`` and ``codex.cmd`` both live in
``%APPDATA%\\npm``, which was on the interactive PATH and not on the inherited
one. So a miss from ``which`` falls through to a sweep of the places npm, bun and
the installers actually put things.

The probe runs the executable, which is a subprocess per tool. It is cached for
that reason, and refreshed only when the user asks.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# The two the header reports. Hermes and OpenCode have tabs but no readout - the
# version question was asked about these.
PROBES: dict[str, str] = {
    "claude": "Claude Code",
    "codex": "Codex",
}

VERSION_ARG = "--version"
# Long enough for a cold npm shim on a spinning disk, short enough that two of
# them in parallel cannot hold the refresh button for more than one.
TIMEOUT = 15.0
_VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][\w.]+)?")

_lock = threading.Lock()
_cache: list[dict] | None = None


def _search_roots() -> list[Path]:
    """Where a globally installed CLI ends up, in the order worth trying."""
    roots: list[Path] = []

    def add(value: str | None, *parts: str) -> None:
        if value:
            roots.append(Path(value).joinpath(*parts))

    if sys.platform == "win32":
        add(os.environ.get("APPDATA"), "npm")
        add(os.environ.get("LOCALAPPDATA"), "npm")
        add(os.environ.get("ProgramFiles"), "nodejs")
        add(os.environ.get("LOCALAPPDATA"), "Programs", "nodejs")
    else:
        add(os.environ.get("HOME"), ".npm-global", "bin")
        roots.append(Path("/usr/local/bin"))
    # Bun and the shell-installer conventions, on every platform.
    add(os.environ.get("USERPROFILE") or os.environ.get("HOME"), ".bun", "bin")
    add(os.environ.get("USERPROFILE") or os.environ.get("HOME"), ".local", "bin")
    return roots


def _suffixes() -> tuple[str, ...]:
    # ``.cmd`` first: an npm install leaves both a shim and an extensionless shell
    # script beside it, and only the shim is runnable from a Windows process.
    return (".cmd", ".exe", ".bat", "") if sys.platform == "win32" else ("",)


def resolve(name: str) -> str:
    """Absolute path to ``name``, or an empty string if it is not installed."""
    found = shutil.which(name)
    if found:
        return found
    for root in _search_roots():
        for suffix in _suffixes():
            candidate = root / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)
    return ""


def _creation_flags() -> int:
    """Keep a console window from flashing over the UI on Windows."""
    flags = 0
    if sys.platform == "win32":
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return flags


def _node_roots() -> list[Path]:
    roots: list[Path] = []
    for value in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if value:
            roots.append(Path(value) / "nodejs")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs" / "nodejs")
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if home:
        roots.append(Path(home) / ".bun" / "bin")
    return roots


def _child_env() -> dict[str, str] | None:
    """The environment to run a probe in, with Node put back if it went missing.

    ``claude.cmd`` and ``codex.cmd`` are npm shims: batch files that invoke
    ``node``. Resolving the shim is therefore only half the job - a PATH without
    the Node directory turns the probe into ``'"node"' is not recognized``, which
    reads like a broken install and is really the same inherited-PATH problem one
    layer down. Confirmed here: ``codex --version`` failed exactly that way from a
    process whose PATH had npm but not nodejs.

    ``None`` means the inherited environment is already fine and does not need to
    be copied.
    """
    if shutil.which("node"):
        return None
    found = next((root for root in _node_roots() if (root / "node.exe").is_file()
                  or (root / "node").is_file()), None)
    if found is None:
        return None
    env = dict(os.environ)
    env["PATH"] = f"{found}{os.pathsep}{env.get('PATH', '')}"
    return env


def probe(name: str, label: str = "") -> dict:
    """Run ``<name> --version`` and read a version out of whatever it says."""
    result = {
        "id": name,
        "label": label or PROBES.get(name, name),
        "version": "",
        "path": "",
        "found": False,
        "error": "",
    }
    executable = resolve(name)
    if not executable:
        result["error"] = "Not installed, or not on this app's PATH."
        return result
    result["path"] = executable
    result["found"] = True

    try:
        completed = subprocess.run(  # noqa: S603 - a path this module resolved
            [executable, VERSION_ARG],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            creationflags=_creation_flags(),
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        result["error"] = f"{name} {VERSION_ARG} did not answer within {int(TIMEOUT)}s."
        return result
    except OSError as exc:
        result["error"] = f"{name} could not be started: {exc.strerror or exc}"
        return result

    # Some CLIs print the version to stderr, and some print it to stdout while
    # also warning on stderr, so both are searched and stdout wins.
    match = _VERSION.search(completed.stdout or "") or _VERSION.search(completed.stderr or "")
    if match:
        result["version"] = match.group(0)
        return result

    # Found on disk but nothing usable came back. That is a different problem from
    # "not installed" - a broken shim, a half-finished upgrade - and saying so is
    # the difference between the user reinstalling and the user fixing their PATH.
    detail = (completed.stderr or completed.stdout or "").strip().splitlines()
    result["error"] = detail[0][:200] if detail else "Installed, but reported no version."
    return result


def probe_all(force: bool = False) -> list[dict]:
    """Every tool in :data:`PROBES`, from cache unless ``force``.

    Blocking. The two probes run side by side, so the wall clock is one process
    launch rather than two, but it is still a process launch - callers on the UI
    thread want :func:`snapshot` and :func:`refresh_async` instead.
    """
    global _cache
    with _lock:
        if _cache is not None and not force:
            return [dict(entry) for entry in _cache]

    names = list(PROBES)
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as pool:
        results = list(pool.map(lambda name: probe(name, PROBES[name]), names))

    with _lock:
        _cache = results
    return [dict(entry) for entry in results]


def clear_cache() -> None:
    global _cache
    with _lock:
        _cache = None


# --------------------------------------------------------------------- async

_worker: threading.Thread | None = None


def _pending() -> list[dict]:
    """What the header shows before the first probe has answered."""
    return [
        {"id": name, "label": label, "version": "", "path": "", "found": False, "error": ""}
        for name, label in PROBES.items()
    ]


def _busy() -> bool:
    return _worker is not None and _worker.is_alive()


def join_worker(timeout: float = 5.0) -> None:
    """Wait for an in-flight probe. For tests; nothing in the app needs it."""
    worker = _worker
    if worker is not None:
        worker.join(timeout=timeout)


def snapshot() -> dict:
    """Current knowledge, without waiting for anything.

    First paint must not block on two subprocess launches, so ``bootstrap`` calls
    this and then :func:`refresh_async`; the UI polls while ``busy`` is true, the
    same way it does for an update check.
    """
    with _lock:
        tools = [dict(entry) for entry in _cache] if _cache is not None else _pending()
    return {"tools": tools, "busy": _busy(), "ready": _cache is not None}


def refresh_async(force: bool = True) -> dict:
    """Start a probe in the background and return immediately."""
    global _worker
    if _busy():
        return snapshot()
    if not force:
        # Read under the lock, answer outside it. ``snapshot`` takes the same
        # non-reentrant lock, so calling it from in here deadlocked the process
        # the moment a second ``force=False`` refresh met a warm cache - which is
        # every ``bootstrap`` after the first, and a webview reload is one.
        with _lock:
            cached = _cache is not None
        if cached:
            return snapshot()

    def run() -> None:
        global _cache
        try:
            probe_all(force=True)
        except Exception as exc:  # noqa: BLE001 - a probe must never take the app down
            # The cache still has to end up set. ``ready`` is "the cache is not
            # None", and the UI polls until it is - so a probe that died silently
            # would leave the header polling every half second forever.
            failed = _pending()
            for entry in failed:
                entry["error"] = f"The version check failed: {type(exc).__name__}."
            with _lock:
                _cache = failed

    _worker = threading.Thread(target=run, name="upool-clis", daemon=True)
    _worker.start()
    return snapshot()
