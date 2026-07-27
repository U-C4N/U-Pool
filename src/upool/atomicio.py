"""Crash-safe file writing.

Config files here belong to *other* programs (Claude Code, Codex). A partial
write would leave a user unable to start their CLI, so every write goes to a
temporary file in the same directory and is swapped in with ``os.replace``,
which is atomic on both POSIX and Windows.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

# Config files can hold API keys, so keep them owner-only where the OS supports it.
SECRET_MODE = stat.S_IRUSR | stat.S_IWUSR


def write_text(path: Path, content: str, *, secret: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if secret and os.name != "nt":
            os.chmod(tmp, SECRET_MODE)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_json(path: Path, data: object, *, secret: bool = False) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", secret=secret)


def read_json(path: Path, default: object | None = None) -> object:
    """Read JSON, returning ``default`` for a missing file.

    A malformed file raises: silently replacing a config we failed to parse
    would destroy whatever the user hand-edited.
    """
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    return json.loads(text)
