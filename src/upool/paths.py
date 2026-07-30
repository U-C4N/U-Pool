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
BACKUP_DIR_NAME = "backups"
UPDATE_DIR_NAME = "update"


def home() -> Path:
    """Root of the user's home directory (override with ``UPOOL_FAKE_HOME``)."""
    override = os.environ.get("UPOOL_FAKE_HOME")
    if override:
        return Path(override)
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
