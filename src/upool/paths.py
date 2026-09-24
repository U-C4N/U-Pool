"""Filesystem locations U-Pool reads from and writes to.

Every path is resolved lazily through a function so tests (and a future
"portable mode") can redirect the whole app by setting ``UPOOL_HOME``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = ".u-pool"
CONFIG_FILE_NAME = "config.json"
SETTINGS_FILE_NAME = "settings.json"
ENV_OWNED_FILE_NAME = "env-owned.json"
CODEX_LOGIN_FILE_NAME = "codex-login.json"
CURSOR_ACCOUNTS_FILE_NAME = "cursor.json"
USAGE_FILE_NAME = "usage.json"
PRICING_FILE_NAME = "pricing.json"
BACKUP_DIR_NAME = "backups"
UPDATE_DIR_NAME = "update"


def home() -> Path:
    """Root of the user's home directory (override with ``UPOOL_FAKE_HOME``).

    Under pytest the override is mandatory rather than polite. Everything below
    this function resolves to files that belong to a working install - the very
    ``~/.claude/settings.json`` the developer's own CLI is reading - so a code
    path that reaches the real home during a test run edits a live machine. That
    is not hypothetical: a throwaway script written to check a ``Path``-clobber
    scenario put a placeholder base URL into a real settings.json and took Claude
    Code down with it. Raising is the only version of this fix that stays fixed;
    a warning would have been read and ignored.
    """
    override = os.environ.get("UPOOL_FAKE_HOME")
    if override:
        return Path(override)
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise RuntimeError(
            "paths.home() reached the real home directory during a test run. Set "
            "UPOOL_FAKE_HOME first - the sandbox fixture in tests/conftest.py does, "
            "and scripts/sandbox.py does it for one-off scripts."
        )
    return Path.home()


def sandboxed() -> bool:
    """Whether the home directory has been redirected for a test or a probe.

    The CLIs below do not all live under the home directory - Hermes resolves
    through ``%LOCALAPPDATA%`` and honours ``HERMES_HOME``, OpenCode honours
    ``OPENCODE_CONFIG`` - so redirecting the home alone would leave two write
    targets pointing at the developer's live install. Every such override is
    therefore ignored while a fake home is in force, which makes the redirect
    airtight by construction rather than by each caller remembering to clear
    three more environment variables.
    """
    return bool(os.environ.get("UPOOL_FAKE_HOME"))


def app_home() -> Path:
    """Where U-Pool keeps its own state (override with ``UPOOL_HOME``)."""
    override = os.environ.get("UPOOL_HOME")
    if override:
        return Path(override)
    return home() / APP_DIR_NAME


def config_file() -> Path:
    return app_home() / CONFIG_FILE_NAME


def settings_file() -> Path:
    return app_home() / SETTINGS_FILE_NAME


def env_owned_file() -> Path:
    """Which environment variables U-Pool set, per namespace.

    Kept out of ``settings.json`` on purpose: it is a record of what was done to
    the machine, not a preference, and it decides what :mod:`upool.winenv` is
    allowed to delete. Restoring a preferences file from elsewhere must not tell
    U-Pool it owns registry values it never wrote.
    """
    return app_home() / ENV_OWNED_FILE_NAME


def codex_login_file() -> Path:
    """Where a ChatGPT login from ``~/.codex/auth.json`` is stashed.

    U-Pool writes that file from scratch and cannot recreate a login it drops,
    so the original is kept here and put back when the official provider wins.
    """
    return app_home() / CODEX_LOGIN_FILE_NAME


def backup_dir() -> Path:
    return app_home() / BACKUP_DIR_NAME


def update_dir() -> Path:
    """Scratch space for the self-updater.

    Under ``app_home`` on purpose: it is always writable, it survives the install
    folder being renamed out from under us, and the swap script that does the
    renaming lives in neither of the directories it touches.
    """
    return app_home() / UPDATE_DIR_NAME


def update_cache_dir() -> Path:
    """Where a downloaded release archive waits, so a retry is free."""
    return update_dir() / "cache"


def claude_dir() -> Path:
    return home() / ".claude"


def claude_settings_file() -> Path:
    return claude_dir() / "settings.json"


def codex_dir() -> Path:
    return home() / ".codex"


def codex_config_file() -> Path:
    return codex_dir() / "config.toml"


def codex_auth_file() -> Path:
    return codex_dir() / "auth.json"


def hermes_dir() -> Path:
    """Where the Hermes CLI keeps its config.

    Hermes does not use a dotfile in the home directory on Windows - it installs
    under ``%LOCALAPPDATA%\\hermes``, which is where the live ``config.yaml`` on
    this machine actually is. ``HERMES_HOME`` wins over both, because that is the
    override Hermes itself honours.
    """
    if sandboxed():
        return home() / ".hermes"
    override = os.environ.get("HERMES_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "hermes"
    return home() / ".hermes"


def hermes_config_file() -> Path:
    return hermes_dir() / "config.yaml"


def opencode_dir() -> Path:
    """``~/.config/opencode`` on every platform, Windows included.

    OpenCode follows the XDG layout rather than the platform convention, so this
    is not ``%APPDATA%`` on Windows - the directory this resolves to is the one
    already sitting in the user's profile.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg and not sandboxed():
        return Path(xdg) / "opencode"
    return home() / ".config" / "opencode"


def opencode_config_file() -> Path:
    """``OPENCODE_CONFIG`` names the file itself, not the directory holding it."""
    override = os.environ.get("OPENCODE_CONFIG")
    if override and not sandboxed():
        return Path(override)
    return opencode_dir() / "opencode.json"


def cursor_app_dir() -> Path:
    """Where the Cursor editor keeps its own state.

    Windows resolves this through ``%APPDATA%``, which is what makes the sandbox
    check load-bearing rather than decorative. The directory underneath holds
    ``state.vscdb`` - the user's entire Cursor history, their composer state and
    their live MCP OAuth secrets - and U-Pool writes into that file. A test or a
    one-off probe that reached the real one would be editing a database the
    running editor owns, so a fake home wins over the platform location outright,
    the same way it does for Hermes and OpenCode - see :func:`sandboxed`.
    """
    if sandboxed():
        return home() / ".cursor-app"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Cursor"
        return home() / "AppData" / "Roaming" / "Cursor"
    if sys.platform == "darwin":
        return home() / "Library" / "Application Support" / "Cursor"
    return home() / ".config" / "Cursor"


def cursor_state_db() -> Path:
    """Cursor's SQLite state, holding the ``cursorAuth/*`` keys a switch rewrites.

    Cursor keeps this open while it runs. That is why a switch closes the editor
    first rather than writing around the lock: the ``-wal`` and ``-shm`` files
    beside it are only in a settled state once the process is gone.
    """
    return cursor_app_dir() / "User" / "globalStorage" / "state.vscdb"


def cursor_storage_json() -> Path:
    """Cursor's telemetry ids - resolved so a switch can prove it left them alone.

    U-Pool does not write this file. Resetting ``telemetry.machineId`` is what
    the reference implementation does to defeat a per-device limit; it is not
    part of changing accounts, and those values are Cursor's own state rather
    than anything U-Pool put there.
    """
    return cursor_app_dir() / "User" / "globalStorage" / "storage.json"


def cursor_accounts_file() -> Path:
    """The Cursor account pool, in its own file rather than a section of config.json.

    ``Store._normalise`` rebuilds its document from ``_empty()`` and copies only
    ``apps``, so an extra top-level section there would be dropped silently on
    the next save - and a Cursor account has no ``Provider`` to normalise through
    anyway. This follows ``env-owned.json`` and ``codex-login.json``: U-Pool
    state that is not a provider record gets its own file.
    """
    return app_home() / CURSOR_ACCOUNTS_FILE_NAME


def usage_file() -> Path:
    """The token-and-cost snapshot. Under app_home() so a session purge, which
    only reaches ~/.claude and ~/.codex, can never delete the history."""
    return app_home() / USAGE_FILE_NAME


def pricing_file() -> Path:
    """User overrides for the per-model price table."""
    return app_home() / PRICING_FILE_NAME


def bundle_root() -> Path:
    """Directory that holds bundled read-only assets (the built UI).

    Under PyInstaller this is the unpacked bundle; from a source checkout it is
    the repository root.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parents[2]


def ui_dist_dir() -> Path:
    """Static Next.js export served to the webview."""
    return bundle_root() / "ui" / "out"
