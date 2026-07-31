"""One current copy of every file a switch overwrites, beside the original.

The ten-deep rotation under ``~/.u-pool/backups/<app>/`` answered a question
nobody asked. What someone wants after a switch they regret is the file as it was
a minute ago, in the folder they already have open - not a timestamped pile in an
app directory they have never visited. So: ``settings.json.backup`` next to
``settings.json``, overwritten every time, restored by renaming it.

Registry values have no folder to sit beside, so the environment snapshot keeps
the app-home location.

A backup that cannot be written is not a reason to abandon the switch it was
protecting, so the failures here are swallowed and reported as "no backup".
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import atomicio, paths, settings

BACKUP_SUFFIX = ".backup"
ENV_SNAPSHOT_NAME = "environment.backup.json"


def enabled() -> bool:
    """Whether the user wants backups at all."""
    return bool(settings.load().get("backup_enabled", True))


def sidecar(source: Path) -> Path | None:
    """Copy ``source`` to ``<name>.backup`` beside it, before it is rewritten.

    ``None`` when backups are off, when there is nothing there yet, or when the
    copy did not work.
    """
    if not enabled() or not source.exists():
        return None
    target = source.with_name(source.name + BACKUP_SUFFIX)
    try:
        shutil.copy2(source, target)
    except OSError:
        return None
    return target


def env_snapshot(values: dict[str, str | None]) -> Path | None:
    """Record environment values as they were immediately before a change.

    ``None`` as a value means the name did not exist, so putting things back by
    hand is a deletion rather than an empty string. One file, overwritten each
    time, holding the last change and not a history.
    """
    if not enabled() or not values:
        return None
    target = paths.backup_dir() / ENV_SNAPSHOT_NAME
    try:
        # API keys travel through here, so it gets the same treatment as a config.
        atomicio.write_json(target, values, secret=True)
    except OSError:
        return None
    return target
