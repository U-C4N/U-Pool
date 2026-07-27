"""Rolling backups of the live config files we overwrite."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import paths

KEEP_PER_FILE = 10


def _slot_dir(app: str) -> Path:
    return paths.backup_dir() / app


def snapshot(app: str, source: Path) -> Path | None:
    """Copy ``source`` into the backup folder before it gets rewritten.

    Returns the backup path, or ``None`` when there was nothing to back up.
    """
    if not source.exists():
        return None
    target_dir = _slot_dir(app)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"{source.name}.{stamp}.bak"
    # Two switches inside one second must not clobber each other's backup.
    counter = 1
    while target.exists():
        target = target_dir / f"{source.name}.{stamp}-{counter}.bak"
        counter += 1
    shutil.copy2(source, target)
    prune(app, source.name)
    return target


def prune(app: str, file_name: str, keep: int = KEEP_PER_FILE) -> None:
    target_dir = _slot_dir(app)
    if not target_dir.exists():
        return
    backups = sorted(
        target_dir.glob(f"{file_name}.*.bak"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[keep:]:
        stale.unlink(missing_ok=True)


def list_backups(app: str) -> list[dict[str, object]]:
    target_dir = _slot_dir(app)
    if not target_dir.exists():
        return []
    entries = []
    for item in sorted(target_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = item.stat()
        entries.append(
            {"path": str(item), "name": item.name, "size": stat.st_size, "mtime": int(stat.st_mtime * 1000)}
        )
    return entries
