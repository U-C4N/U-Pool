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
