"""The usage snapshot - accumulated buckets plus per-file parse progress.

It lives under ``app_home()`` on purpose (spec §4): a different tree from
``~/.claude`` and ``~/.codex``, so a session purge cannot reach it, and the
existing sandbox's ``UPOOL_HOME`` redirect covers it for free. A malformed or
older-versioned file is treated as absent rather than raising - a broken snapshot
is a convenience lost, not a config another program depends on, so the panel
starts empty instead of failing to open.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import atomicio, paths
from .models import KINDS, BucketKey, FileMark, Totals

SNAPSHOT_VERSION = 1


def _usage_path():
    return paths.usage_file()


@dataclass
class Snapshot:
    buckets: dict[BucketKey, Totals] = field(default_factory=dict)
    files: dict[str, FileMark] = field(default_factory=dict)


def load() -> Snapshot:
    try:
        raw = atomicio.read_json(_usage_path(), default=None)
    except ValueError:
        raw = None
    if not isinstance(raw, dict) or raw.get("version") != SNAPSHOT_VERSION:
        return Snapshot()
    snap = Snapshot()
    # A single hand-edited or truncated entry must not sink the rest of a file
    # that otherwise passed the version gate above - drop it, keep going.
    for row in raw.get("buckets", []):
        try:
            key = BucketKey(row["app"], row["date"], row["model"], row["project"])
        except (KeyError, TypeError):
            continue
        t = Totals(messages=row.get("messages", 0))
        for kind in KINDS:
            setattr(t, kind, row.get(kind, 0))
        snap.buckets[key] = t
    for path, mark in (raw.get("files") or {}).items():
        try:
            snap.files[path] = FileMark(size=mark["size"], mtime=mark["mtime"],
                                        offset=mark["offset"], carry=mark.get("carry", ""))
        except (KeyError, TypeError):
            continue
    return snap


def save(snap: Snapshot) -> None:
    payload = {
        "version": SNAPSHOT_VERSION,
        "buckets": [
            {"app": k.app, "date": k.date, "model": k.model, "project": k.project,
             "messages": t.messages, **{kind: getattr(t, kind) for kind in KINDS}}
            for k, t in snap.buckets.items()
        ],
        "files": {
            path: {"size": m.size, "mtime": m.mtime, "offset": m.offset, "carry": m.carry}
            for path, m in snap.files.items()
        },
    }
    atomicio.write_json(_usage_path(), payload)
