"""The unit the panel aggregates on, and the record that makes a rescan cheap.

A ``Totals`` is kept separate from its ``BucketKey`` so the key can stay a frozen,
hashable dict key while the counts underneath it grow across a merge. ``FileMark``
carries ``offset`` for append-only resume and ``carry`` for the one piece of state
a resume cannot otherwise recover - Codex names the model on a line of its own,
which may sit in a chunk already consumed by an earlier pass.
"""

from __future__ import annotations

from dataclasses import dataclass

KINDS = ("input", "output", "cache_read", "cache_write")


@dataclass(frozen=True)
class BucketKey:
    app: str
    date: str  # "YYYY-MM-DD", already in local time
    model: str
    project: str


@dataclass
class Totals:
    messages: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def add(self, messages: int, counts: dict[str, int]) -> None:
        self.messages += messages
        for kind in KINDS:
            setattr(self, kind, getattr(self, kind) + int(counts.get(kind, 0)))


@dataclass
class FileMark:
    size: int
    mtime: float
    offset: int
    carry: str = ""


def bucket_key_of(app: str, date: str, model: str, project: str) -> BucketKey:
    return BucketKey(app=app, date=date, model=model, project=project)


def merge_totals(dst: dict[BucketKey, Totals], src: dict[BucketKey, Totals]) -> None:
    for key, totals in src.items():
        into = dst.get(key)
        if into is None:
            into = Totals()
            dst[key] = into
        into.add(totals.messages, {k: getattr(totals, k) for k in KINDS})
