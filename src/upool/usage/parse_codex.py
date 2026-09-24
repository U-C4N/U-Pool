"""Fold one Codex session into token buckets.

Codex names the model on a ``turn_context`` line of its own and changes it within a
session, so tokens are attributed to the model of the most recent ``turn_context``,
not to the session. That running model - with the session's project and date - is
stashed in ``FileMark.carry`` so an incremental pass whose ``token_count`` sits in a
later chunk than its ``turn_context`` still attributes it correctly. Codex's
``input_tokens`` includes ``cached_input_tokens``, so the cached count is subtracted
back out to keep the four kinds disjoint; ``total_token_usage`` on the same line is
ignored because summing a cumulative field double-counts every turn.
"""

from __future__ import annotations

import json
from datetime import tzinfo
from pathlib import Path

from ..models import APP_CODEX
from .models import BucketKey, FileMark, Totals
from .parse_claude import local_date

_SEP = "\x1f"


def _unpack(carry: str) -> tuple[str, str, str]:
    if not carry:
        return "unknown", "unknown", "unknown"
    model, _, rest = carry.partition(_SEP)
    project, _, date = rest.partition(_SEP)
    return model or "unknown", project or "unknown", date or "unknown"


def parse_file(path: Path, mark: FileMark | None, tz: tzinfo) -> tuple[dict[BucketKey, Totals], FileMark, int]:
    start = mark.offset if mark else 0
    model, project, date = _unpack(mark.carry if mark else "")

    with path.open("rb") as fh:
        fh.seek(start)
        data = fh.read()
    size = path.stat().st_size
    mtime = path.stat().st_mtime
    last_nl = data.rfind(b"\n")
    if last_nl < 0:
        carry = _SEP.join([model, project, date])
        return {}, FileMark(size=size, mtime=mtime, offset=start, carry=carry), 0
    complete = data[: last_nl + 1]
    text = complete.decode("utf-8", errors="replace")

    buckets: dict[BucketKey, Totals] = {}
    skipped = 0
    # split("\n") rather than splitlines(): splitlines() also breaks on U+2028/U+2029
    # and other Unicode line boundaries, which can split a JSON line that contains
    # them and silently drop its tokens. A trailing '\r' from CRLF is removed below.
    for raw in text.split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            skipped += 1
            continue
        kind = obj.get("type")
        payload = obj.get("payload") or {}
        if kind == "session_meta":
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and cwd:
                project = Path(cwd).name
            ts = payload.get("timestamp") or obj.get("timestamp")
            if ts:
                date = local_date(ts, tz)
        elif kind == "turn_context":
            if payload.get("model"):
                model = payload["model"]
        elif kind == "event_msg" and payload.get("type") == "token_count":
            usage = (payload.get("info") or {}).get("last_token_usage") or {}
            if not usage:
                continue
            cached = usage.get("cached_input_tokens", 0) or 0
            counts = {
                "input": max(0, (usage.get("input_tokens", 0) or 0) - cached),
                "output": (usage.get("output_tokens", 0) or 0) + (usage.get("reasoning_output_tokens", 0) or 0),
                "cache_read": cached,
                "cache_write": usage.get("cache_write_input_tokens", 0) or 0,
            }
            key = BucketKey(app=APP_CODEX, date=date, model=model, project=project)
            buckets.setdefault(key, Totals()).add(1, counts)

    carry = _SEP.join([model, project, date])
    return buckets, FileMark(size=size, mtime=mtime, offset=start + len(complete), carry=carry), skipped
