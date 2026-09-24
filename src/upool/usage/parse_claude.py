"""Fold one Claude Code transcript into token buckets.

Every ``type == "assistant"`` line carries a ``message.usage`` with the four token
counts and the model, so the model is per line and no cross-line state is needed -
unlike Codex (see :mod:`parse_codex`), whose ``FileMark.carry`` this parser leaves
empty. Reading is done over bytes so a stored offset resumes exactly at a line
boundary; a half-flushed final line is left behind for the next pass.
"""

from __future__ import annotations

import json
from datetime import datetime, tzinfo
from pathlib import Path

from ..models import APP_CLAUDE
from .models import BucketKey, FileMark, Totals


def local_date(iso: str, tz: tzinfo) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(tz).strftime("%Y-%m-%d")


def project_of(path: Path, obj: dict) -> str:
    cwd = obj.get("cwd")
    if isinstance(cwd, str) and cwd:
        return Path(cwd).name
    # The directory name is a mangled absolute path; its tail is the best guess.
    return path.parent.name.rsplit("-", 1)[-1] or path.parent.name


def parse_file(path: Path, mark: FileMark | None, tz: tzinfo) -> tuple[dict[BucketKey, Totals], FileMark, int]:
    start = mark.offset if mark else 0
    with path.open("rb") as fh:
        fh.seek(start)
        data = fh.read()
    size = path.stat().st_size
    mtime = path.stat().st_mtime
    last_nl = data.rfind(b"\n")
    if last_nl < 0:
        return {}, FileMark(size=size, mtime=mtime, offset=start, carry=""), 0
    complete = data[: last_nl + 1]
    text = complete.decode("utf-8", errors="replace")

    buckets: dict[BucketKey, Totals] = {}
    skipped = 0
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") or {}
        usage = msg.get("usage") or {}
        if not usage:
            continue
        counts = {
            "input": usage.get("input_tokens", 0) or 0,
            "output": usage.get("output_tokens", 0) or 0,
            "cache_read": usage.get("cache_read_input_tokens", 0) or 0,
            "cache_write": usage.get("cache_creation_input_tokens", 0) or 0,
        }
        key = BucketKey(
            app=APP_CLAUDE,
            date=local_date(obj.get("timestamp", ""), tz) if obj.get("timestamp") else "unknown",
            model=msg.get("model") or "unknown",
            project=project_of(path, obj),
        )
        buckets.setdefault(key, Totals()).add(1, counts)

    return buckets, FileMark(size=size, mtime=mtime, offset=start + len(complete), carry=""), skipped
