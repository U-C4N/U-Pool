"""Whether Cursor is running, and how U-Pool asks it to stop.

A switch writes ``state.vscdb`` while Cursor is not holding it open, so this
module exists to answer one question - is the editor still there - and to ask it
once, politely, to leave.

**There is no force kill and there will not be one.** ``taskkill /F`` on an
editor discards whatever it had unsaved, and the account being switched to is
worth less than the file the user was in the middle of. :func:`close` therefore
sends the same close request the quit menu sends and then waits; if Cursor is
still there when the timeout runs out it says so, the switch writes nothing, and
the user is told to close it themselves. A switch that sometimes has to be
retried is better than one that sometimes costs work. A ``force`` parameter
added "for later" is how that guarantee gets lost, so the signature has no room
for one.

Nothing here raises. Every call sits on the path between a button press and a
database write, and a process list that will not answer is a reason to report
that Cursor may still be running - never a reason to take the app down.
:data:`CLOSE_FAILED_NOTE` and :data:`LAUNCH_FAILED_NOTE` are the sentences the
caller puts in ``warnings``; this module returns booleans and nothing else, so
that ``tests/conftest.py`` can stub all three entry points by signature.

Windows is the platform this was written on and the only one it has been tried
on. macOS and Linux resolve through ``pgrep``/``pkill`` and ``open`` so the code
is not Windows-shaped - not because either has been exercised.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .. import clis

# Every Electron process in the tree carries this image name on Windows, which
# matters for both halves of the job - see :func:`_windows_close_request`.
PROCESS_NAME = "Cursor.exe"
# ``pgrep -x`` anchors the pattern to the whole process name, so this matches the
# main process and not ``Cursor Helper (Renderer)``.
POSIX_PATTERN = "[Cc]ursor"
# The launcher Cursor puts on the PATH, as opposed to the executable itself.
CLI_NAME = "cursor"

CLOSE_TIMEOUT = 10.0
POLL_INTERVAL = 0.25
# ``tasklist`` enumerates every process on the machine; five seconds means a
# machine already in trouble, and blocking the UI on it any longer is worse than
# answering the question wrongly - see :func:`running`.
COMMAND_TIMEOUT = 5.0

CLOSE_FAILED_NOTE = (
    "Cursor did not close - you may have unsaved changes. "
    "Close it yourself and try again."
)
LAUNCH_FAILED_NOTE = (
    "The account was switched, but Cursor could not be started from here - "
    "open it yourself to sign in as the new account."
)


def running() -> bool:
    """Is Cursor running right now?

    A probe that will not run at all answers no. Treating "could not tell" as
    running is the tempting choice, but it would make every switch on such a
    machine permanently impossible, and the cost of being wrong the other way is
    bounded: SQLite serialises the write, so the worst case is a switch that does
    not stick because the editor rewrites its own keys as it exits.
    """
    if sys.platform == "win32":
        return _windows_running()
    return _posix_running()


def close(timeout: float = CLOSE_TIMEOUT) -> bool:
    """Ask Cursor to quit and wait for it. ``True`` if it is gone when we return.

    The caller must read ``False`` as "write nothing". The failure mode of a
    half-applied auth record is an editor that will not sign in and cannot be
    signed out, and there is no way back from it that does not involve the user
    finding a backup file.
    """
    if not running():
        return True

    if sys.platform == "win32":
        _windows_close_request()
    else:
        _posix_close_request()

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if not running():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)


def launch() -> bool:
    """Start Cursor again, reporting whether we managed to.

    ``False`` is not a failed switch. By the time this runs the account has
    already been written, so a Cursor that cannot be found is a warning on a
    switch that worked - which is why nothing here raises and why the caller
    carries :data:`LAUNCH_FAILED_NOTE` rather than rolling anything back.
    """
    command = _launch_command()
    if not command:
        return False
    try:
        subprocess.Popen(  # noqa: S603 - a path this module resolved
            command,
            close_fds=True,
            **_detached(),
        )
    except (OSError, ValueError):
        return False
    return True


# ------------------------------------------------------------------- subprocess


def _creation_flags() -> int:
    """Keep a console window from flashing over the UI on Windows.

    U-Pool runs windowed, and :func:`running` is polled - four times a second for
    the whole of a close - so a flash here would not be a cosmetic detail, it
    would be a strobe. Same constant and same reason as :mod:`upool.clis`.
    """
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _system32(name: str) -> str:
    """An absolute path to a Windows built-in rather than whatever the PATH finds.

    ``CreateProcess`` searches the calling executable's own directory and the
    current directory before the system one, so a bare ``tasklist`` would run a
    file of that name dropped beside ``U-Pool.exe``. These two tools decide
    whether an editor gets closed; they come from System32 or not at all.
    """
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    candidate = Path(root) / "System32" / name
    return str(candidate) if candidate.is_file() else name


def _run(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run one probe, or return ``None`` if it could not be run at all.

    ``errors="replace"`` is here because these tools answer in the console
    codepage while Python decodes with the ANSI one. On a localised Windows the
    two disagree, and a mis-decoded "no such task" message must not become an
    exception on the path that closes an editor.
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed argument lists, no user input
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=COMMAND_TIMEOUT,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------- windows


def _windows_running() -> bool:
    """Ask ``tasklist`` whether anything is running under :data:`PROCESS_NAME`.

    Enumeration goes through a subprocess rather than
    ``CreateToolhelp32Snapshot``: a page of ``ctypes`` structure definitions to
    save a tenth of a second on a poll is a poor trade in the module whose other
    job is deciding whether an editor gets closed.
    """
    completed = _run(
        [
            _system32("tasklist.exe"),
            "/FI",
            f"IMAGENAME eq {PROCESS_NAME}",
            "/FO",
            "CSV",
            "/NH",
        ]
    )
    if completed is None:
        return False
    # The "no matching task" line is localised - this machine's Windows says it in
    # Turkish - and the exit status is zero either way, so the image name is the
    # only part of the answer worth matching on.
    return PROCESS_NAME.lower() in (completed.stdout or "").lower()


def _windows_close_request() -> None:
    """``taskkill /IM``, and never ``/F``. See the module docstring.

    The result is deliberately ignored. Cursor runs a dozen helper processes under
    the same image name and none of them owns a window, so ``taskkill`` reports a
    failure for each one while the process that matters is closing normally.
    Whether Cursor is actually gone is a question only :func:`running` can answer.
    """
    _run([_system32("taskkill.exe"), "/IM", PROCESS_NAME])


def _windows_installs() -> tuple[Path, ...]:
    """Where the Cursor installer leaves ``Cursor.exe``, per-user first."""
    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "cursor" / PROCESS_NAME)
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        # The all-users install, for a machine where an admin set Cursor up.
        candidates.append(Path(program_files) / "Cursor" / PROCESS_NAME)
    return tuple(candidates)


# ------------------------------------------------------------------------ posix


def _posix_running() -> bool:
    completed = _run(["pgrep", "-x", POSIX_PATTERN])
    return completed is not None and completed.returncode == 0


def _posix_close_request() -> None:
    """SIGTERM, which is ``pkill``'s default - the signal an editor can catch.

    Never ``-9``: a signal the process cannot handle is the POSIX spelling of the
    force kill this module refuses.
    """
    _run(["pkill", "-x", POSIX_PATTERN])


# ----------------------------------------------------------------------- launch


def _launch_command() -> list[str]:
    """The argv that starts Cursor, or ``[]`` if it could not be found."""
    if sys.platform == "darwin":
        app = Path("/Applications/Cursor.app")
        # ``open`` hands the launch to LaunchServices, so the editor comes up as a
        # normal foreground application instead of a child of the process that
        # just asked it to quit.
        return ["/usr/bin/open", "-a", str(app)] if app.is_dir() else []

    if sys.platform == "win32":
        for candidate in _windows_installs():
            if candidate.is_file():
                return [str(candidate)]

    # ``clis.resolve`` is ``shutil.which`` plus a sweep of the install locations,
    # and the sweep is here for the reason that module documents: U-Pool is a
    # windowed process launched from Explorer or from the sign-in entry, so it
    # inherits the PATH as it stood at logon rather than the one a terminal has.
    found = clis.resolve(CLI_NAME)
    return [found] if found else []


def _detached() -> dict[str, Any]:
    """Start Cursor and stop caring about it.

    U-Pool exits when the user closes its window, and an editor that went down
    with the launcher would make a completed switch look like a crash. On Windows
    this is the flag set :func:`upool.updater.spawn_swap` already uses; on POSIX a
    new session keeps a signal sent to U-Pool's process group away from Cursor.
    """
    if sys.platform == "win32":
        flags = 0
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
            flags |= getattr(subprocess, name, 0)
        return {"creationflags": flags}
    return {"start_new_session": True}
