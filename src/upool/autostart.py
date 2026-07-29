"""Launch U-Pool when the user signs in.

Windows only, and deliberately the least invasive mechanism available: one value
under ``HKCU\\...\\Run``. It needs no elevation, it shows up in Task Manager >
Startup apps where the user expects to find it, and switching the toggle off
deletes it again - nothing is left behind.

macOS and Linux report themselves unsupported so the UI can grey the switch out
instead of pretending it worked.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from .models import UPoolError

VALUE_NAME = "U-Pool"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Turning a startup app off in Task Manager does not delete the Run value - it
# records the veto here, keyed by the same name, and Windows then skips the
# entry. The two keys can disagree, so both are read before reporting a state.
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
# The veto lives in the low bit of the blob's first byte: 0x02/0x06 approved,
# 0x03/0x07 switched off. Reading the bit rather than matching known bytes keeps an
# unfamiliar blob from being reported as a veto that is not there.
VETO_BIT = 0x01
# Where Windows lets the user undo their own veto.
SETTINGS_URI = "ms-settings:startupapps"

UNSUPPORTED_NOTE = "Launching at sign-in is wired up for Windows only."
BLOCKED_NOTE = (
    "Windows has this entry switched off under Task Manager > Startup apps. "
    "Only Windows can switch it back on."
)
READY_NOTE = "One entry under HKCU\\...\\CurrentVersion\\Run. No admin rights needed."
SOURCE_NOTE = (
    "Running from source, so the entry starts `pythonw -m upool` - "
    "that needs U-Pool installed (pip install -e .)."
)


def supported() -> bool:
    return sys.platform == "win32"


def command() -> str:
    """The command line to register.

    Explorer hands this string to ``CreateProcess`` with no separate application
    name, so the executable is whatever comes before the first space: an unquoted
    ``C:\\Program Files\\...`` path would look for ``C:\\Program.exe`` first.
    ``list2cmdline`` does the quoting the same way the OS undoes it.
    """
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([str(executable)])
    # pythonw.exe is the GUI build of the interpreter: no console window at sign-in.
    launcher = executable.with_name("pythonw.exe")
    if not launcher.is_file():
        launcher = executable
    return subprocess.list2cmdline([str(launcher), "-m", "upool"])


def state() -> dict[str, Any]:
    """Everything the settings panel needs to render the switch.

    Three states, not two: off, on, and "registered but Windows is ignoring it".
    Never raises - a registry that will not answer is reported as off with the
    reason attached, because this runs during the first paint of the UI.
    """
    if not supported():
        return {
            "supported": False,
            "enabled": False,
            "blocked": False,
            "command": "",
            "detail": UNSUPPORTED_NOTE,
        }
    try:
        registered = _read_run_value()
        blocked = bool(registered) and _blocked_by_windows()
    except UPoolError as exc:
        return {
            "supported": True,
            "enabled": False,
            "blocked": False,
            "command": command(),
            "detail": str(exc),
        }
    detail = READY_NOTE if getattr(sys, "frozen", False) else SOURCE_NOTE
    return {
        "supported": True,
        # Whether *our* entry exists - not whether Windows is honouring it. A veto
        # reported as "off" would leave the switch with no gesture that removes the
        # entry, because the only thing an off switch can send is "on".
        "enabled": bool(registered),
        "blocked": blocked,
        "command": registered or command(),
        "detail": BLOCKED_NOTE if blocked else detail,
    }


def is_enabled() -> bool:
    return bool(state()["enabled"])


def apply(enabled: bool) -> None:
    """Add or remove the sign-in entry.

    A veto recorded by Task Manager is deliberately left alone: quietly clearing
    it would take a decision the user made in Windows' own UI away from them, and
    that is exactly the move malware makes to stay resident. :func:`state` reports
    the veto instead so the app can say so and point at the Windows switch.
    """
    if not supported():
        raise UPoolError(UNSUPPORTED_NOTE)
    if enabled:
        _write_run_value(command())
    else:
        _delete_run_value()


def reconcile(desired: bool) -> bool:
    """Point an existing entry at the copy of U-Pool that is running now.

    A bundle that was moved, renamed or reinstalled elsewhere would otherwise keep
    a startup entry that silently fails. Only ever *repoints* - an entry deleted
    outside U-Pool (regedit, an autoruns tool) stays deleted, the same respect the
    Task Manager veto gets. Returns whether anything changed.
    """
    if not supported() or not desired:
        return False
    try:
        current = _read_run_value()
        wanted = command()
        if not current or current == wanted:
            return False
        _write_run_value(wanted)
    except UPoolError:
        return False
    return True


# --------------------------------------------------------------------- registry


def _winreg():
    import winreg  # noqa: PLC0415 - Windows-only, imported behind supported()

    return winreg


def _read_run_value() -> str:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise UPoolError(f"Could not read the Windows startup entry: {exc}") from exc
    return str(value)


def _write_run_value(value: str) -> None:
    winreg = _winreg()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, value)
    except OSError as exc:
        raise UPoolError(f"Could not write the Windows startup entry: {exc}") from exc


def _delete_run_value() -> None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise UPoolError(f"Could not remove the Windows startup entry: {exc}") from exc


def _blocked_by_windows() -> bool:
    """Has the user switched this entry off in Task Manager?

    No record at all means approved - that is the default for a fresh entry.
    """
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_QUERY_VALUE
        ) as key:
            data, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return False
    if not isinstance(data, (bytes, bytearray)) or len(data) == 0:
        return False
    return bool(data[0] & VETO_BIT)
