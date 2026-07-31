"""The user's Windows environment variables, as a target U-Pool manages.

The CLIs this app configures do not read their config file alone. Codex resolves
``env_key = "codefast"`` against the process environment, and Claude Code honours
``ANTHROPIC_*`` from it too, so a switch that rewrites files and nothing else
leaves the next shell on the previous provider. ``HKCU\\Environment`` - the user
half of the environment, no elevation needed - is the only lever that reaches
them.

Nothing here raises. Registry trouble degrades a switch, it does not fail it, so
every entry point returns a value and :func:`apply` carries the reason in
:attr:`EnvResult.error`.

The ownership file is the property that matters most: U-Pool removes only names
it recorded as its own. The author's hand-set ``MINIMAX_CN_API_KEY``,
``YUNWU_API_KEY`` and ``BLANKAPI_API_KEY`` live in the same key and must survive
every path through this module.

Namespaces are ``"anthropic"`` and ``"openai"`` - a namespace rather than an app
id because Claude Code and Claude Desktop share one set of variable names, and
there is no second Anthropic set to give them.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from . import claims

# Redirected onto a scratch key by the test suite, the way ``autostart.RUN_KEY``
# is, so a test run can never touch the environment the developer works in.
ENV_KEY = r"Environment"

# The section name WM_SETTINGCHANGE carries is fixed by Windows and only happens
# to spell the same word as ENV_KEY. Redirecting the key must not redirect this.
BROADCAST_SECTION = "Environment"

HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x1A
SMTO_ABORTIFHUNG = 0x0002
BROADCAST_TIMEOUT_MS = 5000

UNSUPPORTED_NOTE = "Environment variables are wired up for Windows only."


@dataclass
class EnvResult:
    """What :func:`apply` actually did, in the same spirit as ``ApplyResult``."""

    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    # Names that already existed and now answer to U-Pool. Worth reporting: the
    # user set them by hand once and would otherwise not know they moved.
    adopted: list[str] = field(default_factory=list)
    unsupported: bool = False
    error: str = ""


def supported() -> bool:
    return sys.platform == "win32"


def read_all() -> dict[str, str]:
    """Every value under the user's environment key, empty when it cannot be read."""
    if not supported():
        return {}
    winreg = _winreg()
    values: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    # The end of the list arrives as an error, not a sentinel.
                    break
                values[str(name)] = "" if value is None else str(value)
                index += 1
    except OSError:
        return {}
    return values


def read(name: str) -> str | None:
    """One value, or ``None`` when it is not set."""
    if not supported() or not name:
        return None
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return "" if value is None else str(value)


def owned(namespace: str) -> list[str]:
    """The names U-Pool set for ``namespace`` - and so the only ones it may remove."""
    return claims.read(namespace)


def preview(namespace: str, desired: dict[str, str]) -> dict[str, str | None]:
    """What :func:`apply` would do: the new value per name, ``None`` for a removal.

    Bookkeeping over the ownership file, so it answers off Windows too - the
    caller decides whether a plan that cannot run is worth showing.
    """
    plan: dict[str, str | None] = dict(_clean(desired))
    wanted = {name.lower() for name in plan}
    for name in owned(namespace):
        if name.lower() not in wanted:
            plan[name] = None
    return plan


def apply(namespace: str, desired: dict[str, str]) -> EnvResult:
    """Make ``desired`` the whole of what U-Pool sets for ``namespace``.

    Writes every wanted name, then removes the names it wrote last time that are
    not wanted any more, then records the new list. A name U-Pool did not write
    is never removed, whatever it is called and whatever ``desired`` holds.
    """
    result = EnvResult()
    if not supported():
        result.unsupported = True
        return result
    try:
        wanted = _clean(desired)
        wanted_keys = {name.lower() for name in wanted}
        # Registry names are case-insensitive, so every comparison goes through a
        # folded key while the registry's own spelling is what gets reported.
        present = {name.lower(): name for name in read_all()}
        previous = owned(namespace)
        held = {name.lower() for name in previous}
        problems: list[str] = []

        # Names still ours after this call. Seeded from what actually landed, so
        # a value that would not write is not claimed.
        keeps: list[str] = []
        for name, value in wanted.items():
            try:
                _write(name, value)
            except OSError as exc:
                problems.append(f"{name}: {exc}")
                continue
            result.written.append(name)
            keeps.append(name)
            if name.lower() in present and name.lower() not in held:
                result.adopted.append(present[name.lower()])

        for name in previous:
            if name.lower() in wanted_keys:
                continue
            if name.lower() not in present:
                # Someone deleted it outside U-Pool. Drop the claim, say nothing.
                continue
            try:
                _delete(present[name.lower()])
            except OSError as exc:
                problems.append(f"{name}: {exc}")
                # Still set, so still ours to clean up on the next switch.
                keeps.append(name)
                continue
            result.removed.append(present[name.lower()])

        claims.write(namespace, keeps)
        if result.written or result.removed:
            _broadcast()
        if problems:
            result.error = "; ".join(problems)
    except Exception as exc:  # noqa: BLE001 - a switch must not fail over a variable
        result.error = str(exc) or exc.__class__.__name__
    return result


# --------------------------------------------------------------------- registry


def _winreg():
    import winreg  # noqa: PLC0415 - Windows-only, imported behind supported()

    return winreg


def _write(name: str, value: str) -> None:
    winreg = _winreg()
    # A value holding %USERPROFILE% has to be REG_EXPAND_SZ or every program
    # reading it gets the percent signs instead of the path.
    kind = winreg.REG_EXPAND_SZ if "%" in value else winreg.REG_SZ
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, kind, value)


def _delete(name: str) -> None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        return


def _broadcast() -> None:
    """Announce the change to the running desktop.

    Explorer hands the refreshed block to every process it starts afterwards, so
    a new terminal sees the switch without a sign-out. Best effort by design: a
    window that will not answer inside the timeout has not undone the write.
    """
    try:
        import ctypes  # noqa: PLC0415 - Windows-only, reached after a write
        from ctypes import wintypes  # noqa: PLC0415

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendMessageTimeoutW.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPCWSTR,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(wintypes.DWORD),
        )
        user32.SendMessageTimeoutW.restype = wintypes.LPARAM
        answered = wintypes.DWORD()
        user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            BROADCAST_SECTION,
            SMTO_ABORTIFHUNG,
            BROADCAST_TIMEOUT_MS,
            ctypes.byref(answered),
        )
    except Exception:  # noqa: BLE001 - nothing about a switch depends on this
        return


# -------------------------------------------------------------------- ownership


def _clean(desired: dict[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for name, value in dict(desired or {}).items():
        key = str(name).strip()
        if key:
            clean[key] = "" if value is None else str(value)
    return clean
