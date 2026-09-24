"""Turn the transcripts on disk into the snapshot, and the snapshot into aggregates.

``refresh`` is the only writer: it rescans, merges and saves. ``summary`` is a pure
read over the saved snapshot - the panel calls it on every paint, so it must not
touch the transcript directories. A file that has vanished since the last scan (the
purge case) loses its mark but keeps its buckets, which is the whole reason the
history outlives a purge.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from pathlib import Path
from typing import Iterator

from .. import paths
from ..models import APP_CLAUDE, APP_CODEX
from . import parse_claude, parse_codex, store
from .models import KINDS, BucketKey, Totals, merge_totals
from .pricing import Pricing


def _local_tz(tz: tzinfo | None) -> tzinfo:
    return tz if tz is not None else datetime.now().astimezone().tzinfo


def _iter_transcripts(app: str | None) -> Iterator[tuple[str, Path]]:
    if app in (None, APP_CLAUDE):
        root = paths.claude_dir() / "projects"
        if root.is_dir():
            for f in root.glob("*/*.jsonl"):
                yield APP_CLAUDE, f
    if app in (None, APP_CODEX):
        root = paths.codex_dir() / "sessions"
        if root.is_dir():
            for f in root.rglob("rollout-*.jsonl"):
                yield APP_CODEX, f


def refresh(app: str | None = None, tz: tzinfo | None = None) -> store.Snapshot:
    tz = _local_tz(tz)
    snap = store.load()
    seen: set[str] = set()

    for which, path in _iter_transcripts(app):
        key = str(path)
        seen.add(key)
        try:
            st = path.stat()
        except OSError:
            continue
        mark = snap.files.get(key)
        if mark and mark.size == st.st_size and mark.mtime == st.st_mtime:
            continue  # unchanged
        if mark and (st.st_size < mark.size or st.st_mtime < mark.mtime):
            mark = None  # shrunk/replaced: reparse from zero
        parse = parse_claude.parse_file if which == APP_CLAUDE else parse_codex.parse_file
        try:
            buckets, new_mark, _ = parse(path, mark, tz)
        except OSError:
            continue
        merge_totals(snap.buckets, buckets)
        snap.files[key] = new_mark

    # Marks whose file is gone are dropped; their buckets stay (the purge case).
    # Only prune within the app(s) just scanned, so a single-app refresh does not
    # forget the other app's still-live files.
    for key in list(snap.files):
        in_scope = (app is None) or (
            (app == APP_CLAUDE and (paths.claude_dir() / "projects") == Path(key).parent.parent)
            or (app == APP_CODEX and "sessions" in Path(key).parts)
        )
        if in_scope and key not in seen:
            del snap.files[key]

    store.save(snap)
    return snap


def _month_prefix(tz: tzinfo) -> str:
    return datetime.now(tz).strftime("%Y-%m")


def _cutoff(range_key: str, tz: tzinfo) -> str:
    if range_key == "all":
        return ""
    days = {"7d": 7, "30d": 30}.get(range_key, 30)
    from datetime import timedelta
    return (datetime.now(tz) - timedelta(days=days)).strftime("%Y-%m-%d")


def summary(range_key: str = "30d", tz: tzinfo | None = None) -> dict:
    tz = _local_tz(tz)
    snap = store.load()
    pricing = Pricing.load()
    cutoff = _cutoff(range_key, tz)
    month = _month_prefix(tz)

    tiles = {APP_CLAUDE: _tile(), APP_CODEX: _tile()}
    series: dict[str, dict] = {}
    by_model: dict[tuple[str, str], dict] = {}
    by_project: dict[tuple[str, str], dict] = {}

    for key, t in snap.buckets.items():
        counts = {k: getattr(t, k) for k in KINDS}
        tokens = sum(counts.values())
        cost = pricing.cost(key.model, counts)
        # Tiles: this-month and all-time, per app, regardless of the range filter.
        # The tile itself carries the all-time totals at its top level, with the
        # current month's slice nested under "this_month" - both are always
        # reported, independent of the range_key the caller asked summary() for.
        tile = tiles.setdefault(key.app, _tile())
        _tile_add(tile, tokens, cost)
        if key.date.startswith(month):
            _tile_add(tile["this_month"], tokens, cost)
        # Everything below respects the range filter.
        if cutoff and key.date < cutoff:
            continue
        day = series.setdefault(key.date, {"date": key.date, "claude": 0.0, "codex": 0.0, "tokens": 0})
        day[key.app] = day.get(key.app, 0.0) + (cost or 0.0)
        day["tokens"] += tokens
        _row(by_model, (key.app, key.model), {"app": key.app, "model": key.model}, counts, tokens, cost)
        _row(by_project, (key.app, key.project), {"app": key.app, "project": key.project}, counts, tokens, cost)

    return {
        "as_of": datetime.now(tz).isoformat(timespec="seconds"),
        "tiles": tiles,
        "series": sorted(series.values(), key=lambda d: d["date"]),
        "by_model": sorted(by_model.values(), key=lambda r: r["tokens"], reverse=True),
        "by_project": sorted(by_project.values(), key=lambda r: r["tokens"], reverse=True),
    }


def _tile() -> dict:
    return {"tokens": 0, "cost": None, "this_month": {"tokens": 0, "cost": None}}


def _tile_add(slot: dict, tokens: int, cost: float | None) -> None:
    slot["tokens"] += tokens
    if cost is not None:
        slot["cost"] = round((slot["cost"] or 0.0) + cost, 6)


def _row(bag: dict, key, base: dict, counts: dict, tokens: int, cost: float | None) -> None:
    row = bag.get(key)
    if row is None:
        row = {**base, **{k: 0 for k in KINDS}, "tokens": 0, "cost": None}
        bag[key] = row
    for k in KINDS:
        row[k] += counts[k]
    row["tokens"] += tokens
    if cost is not None:
        row["cost"] = round((row["cost"] or 0.0) + cost, 6)
