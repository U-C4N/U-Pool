"""Deleting the conversation transcripts a CLI has accumulated.

This is the one feature in U-Pool that destroys data on purpose, so the scope is
a fixed table rather than a pattern, a scan or a setting. :data:`TARGETS` is the
whole of what "delete all sessions" means, and adding to it is the only way to
widen it.

What is deliberately *not* in the table is the point of the table. Both CLIs keep
their transcripts in the same directory as their credentials, their settings,
their plugins and their skills - ``~/.claude`` holds ``settings.json`` and
``.credentials.json``; ``~/.codex`` holds ``auth.json`` and ``config.toml``. A
glob over either would take a login with it. So would sweeping "everything that
looks like a cache": ``~/.claude/file-history`` is how Claude Code undoes an edit
and ``~/.codex/.tmp`` was 113 MB of live scratch space on the machine this was
written for.

Nothing is backed up. A copy of 230 MB of transcripts written somewhere the user
did not ask for is not a safety net, it is the same data twice, and the UI says
as much before it asks.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import paths
from .models import APP_CLAUDE, APP_CODEX, UPoolError


@dataclass(frozen=True)
class Target:
    """One thing to delete. ``tree`` empties a directory but keeps the directory."""

    resolve: Callable[[], Path]
    tree: bool


TARGETS: dict[str, tuple[Target, ...]] = {
    APP_CLAUDE: (
        # Every transcript, one directory per project.
        Target(lambda: paths.claude_dir() / "projects", True),
        Target(lambda: paths.claude_dir() / "sessions", True),
        # The prompt history the up-arrow walks.
        Target(lambda: paths.claude_dir() / "history.jsonl", False),
    ),
    APP_CODEX: (
        Target(lambda: paths.codex_dir() / "sessions", True),
        Target(lambda: paths.codex_dir() / "archived_sessions", True),
        Target(lambda: paths.codex_dir() / "history.jsonl", False),
        # Codex looks sessions up through this rather than by walking the folder,
        # so leaving it behind would list transcripts that are no longer there.
        Target(lambda: paths.codex_dir() / "session_index.jsonl", False),
    ),
}

SUPPORTED = tuple(TARGETS)


def _targets(app: str) -> tuple[Target, ...]:
    try:
        return TARGETS[app]
    except KeyError:
        raise UPoolError(f"U-Pool does not track sessions for '{app}'.") from None


def _measure(path: Path) -> tuple[int, int]:
    """``(files, bytes)`` under ``path``, counting the file itself if it is one."""
    if not path.exists():
        return 0, 0
    if path.is_file():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 1, 0
    files = 0
    total = 0
    for entry in path.rglob("*"):
        if not entry.is_file():
            continue
        files += 1
        try:
            total += entry.stat().st_size
        except OSError:
            # A file that vanished or cannot be stat'd still counts as one file;
            # the size is the part that is unknowable, not its existence.
            pass
    return files, total


def summary(app: str) -> dict:
    """What a purge would delete, so the button can say it before it is pressed."""
    entries = []
    files = 0
    total = 0
    for target in _targets(app):
        path = target.resolve()
        count, size = _measure(path)
        files += count
        total += size
        entries.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "files": count,
                "bytes": size,
            }
        )
    return {"app": app, "entries": entries, "files": files, "bytes": total}


def _guard(path: Path) -> None:
    """Refuse anything that is not under the home directory U-Pool resolved.

    The paths come from a literal table, so this cannot fire for a value a user
    supplied - it fires if ``paths.home()`` is ever made to return something
    unexpected, which is exactly the failure that would make a delete unbounded.
    """
    root = paths.home().resolve()
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise UPoolError(f"{path} could not be resolved.") from exc
    if resolved == root or root not in resolved.parents:
        raise UPoolError(f"{path} is outside the home directory and was not touched.")


def purge(app: str) -> dict:
    """Delete every target for ``app``, reporting what went and what would not.

    One failure does not abandon the rest. A transcript held open by a running
    CLI is the ordinary case, and leaving the other 900 files in place because of
    it would make the button unreliable rather than safe.
    """
    deleted = 0
    freed = 0
    errors: list[str] = []

    for target in _targets(app):
        path = target.resolve()
        _guard(path)
        if not path.exists():
            continue
        # Measured first: once it is gone there is nothing left to count.
        count, size = _measure(path)
        if target.tree:
            removed, failures = _empty(path)
            deleted += removed
            freed += size if not failures else _reclaimed(path, size)
            errors.extend(failures)
        else:
            try:
                path.unlink()
            except OSError as exc:
                errors.append(f"{path}: {exc.strerror or exc}")
            else:
                deleted += count
                freed += size

    return {"app": app, "deleted": deleted, "freed": freed, "errors": errors}


def _empty(path: Path) -> tuple[int, list[str]]:
    """Remove the contents of ``path``, keeping the directory itself.

    The directory stays because the CLI expects to find it: Claude Code creates
    ``projects`` on demand, but Codex has been known to trip over a missing
    ``sessions``, and an empty directory costs nothing.
    """
    deleted = 0
    errors: list[str] = []
    for entry in sorted(path.iterdir()):
        count, _ = _measure(entry)
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError as exc:
            errors.append(f"{entry}: {exc.strerror or exc}")
        else:
            deleted += count
    return deleted, errors


def _reclaimed(path: Path, before: int) -> int:
    """How much of ``before`` actually went, for a partial delete."""
    _, after = _measure(path)
    return max(0, before - after)
