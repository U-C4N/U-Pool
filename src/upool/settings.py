"""App preferences - the switches that belong to U-Pool itself.

``~/.u-pool/settings.json`` is deliberately separate from ``config.json``: losing
a preference is a shrug, losing the provider list with its API keys is not, so a
damaged preferences file falls back to the defaults instead of raising.

Keys U-Pool does not know are written back untouched, so a file from a newer
build survives a downgrade.
"""

from __future__ import annotations

from typing import Any

from . import atomicio, paths
from .models import as_bool

# Launching at sign-in is the user's choice; the OS-level entry is owned by
# :mod:`upool.autostart` and this is only the remembered intent.
DEFAULTS: dict[str, Any] = {
    "launch_at_startup": False,
}


def load() -> dict[str, Any]:
    """Preferences with every default filled in."""
    try:
        raw = atomicio.read_json(paths.settings_file(), None)
    except Exception:  # noqa: BLE001 - a preference must never stop the app starting
        raw = None
    data = dict(DEFAULTS)
    if isinstance(raw, dict):
        data.update(raw)
    data["launch_at_startup"] = as_bool(data.get("launch_at_startup"))
    return data


def update(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into the stored preferences and return the new state."""
    data = load()
    data.update(patch)
    atomicio.write_json(paths.settings_file(), data)
    return data
