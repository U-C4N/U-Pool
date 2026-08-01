"""Reading a YAML config, and rewriting only the parts of it we own.

Hermes keeps everything in one ``config.yaml`` - the provider list, but also the
toolsets, the kanban board, the Slack and Discord credentials, thirty-odd other
sections. Loading that document and dumping it back would preserve every value
and still rewrite every line: quoting style, flow vs block, wrapping, blank-line
grouping are all the dumper's choice, not the file's. A switch that produces a
700-line diff to change six of them is one nobody can review, and it is the same
mistake the Codex adapter already stopped making with TOML.

So the read is a parse and the write is a splice. :func:`sections` finds the line
range each top-level key occupies, :func:`splice` swaps in freshly serialised
text for just the named ones, and every other byte of the file survives exactly
as it was found - including anything this module could not have round-tripped,
like an anchor or a merge key in a section U-Pool never touches.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .models import UPoolError

# A top-level key starts at column zero. Indented lines belong to whichever key
# came before them, and that is the whole of the grammar this needs: the ranges
# are only ever used to replace a section wholesale.
_TOP_LEVEL = re.compile(r"^([A-Za-z_][\w.-]*)\s*:")
# Document markers and comments sitting between sections belong to neither, so a
# range stops before them rather than swallowing them into the previous key.
_BOUNDARY = re.compile(r"^(---|\.\.\.)\s*$")


def read(path: Path) -> dict:
    """Parse ``path``, or refuse the whole operation.

    A file that will not parse is left alone rather than replaced: nothing can be
    preserved out of a document that could not be read, so the caller has to stop
    rather than write a version assembled from the provider record alone.
    """
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise UPoolError(
            f"{path} is not valid YAML, so it was left alone. Fix it and try again."
        ) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise UPoolError(f"{path} does not contain a YAML mapping.")
    return data


def sections(text: str) -> dict[str, tuple[int, int]]:
    """Half-open line range of every top-level key, in the order they appear."""
    lines = text.splitlines()
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        if not line or line[0] in " \t#":
            continue
        if _BOUNDARY.match(line):
            # A document marker ends the section before it without starting one.
            starts.append(("", index))
            continue
        match = _TOP_LEVEL.match(line)
        if match:
            starts.append((match.group(1), index))

    ranges: dict[str, tuple[int, int]] = {}
    for position, (key, start) in enumerate(starts):
        if not key:
            continue
        end = starts[position + 1][1] if position + 1 < len(starts) else len(lines)
        # Blank lines and column-zero comments trailing a section read as
        # separators for the *next* one, so they are left outside the range and
        # survive a replacement rather than being carried off with it.
        while end > start + 1:
            previous = lines[end - 1]
            if previous.strip() and not previous.startswith("#"):
                break
            end -= 1
        # A duplicate top-level key is invalid YAML that PyYAML accepts with
        # last-wins semantics. Matching that here keeps the splice pointed at the
        # copy the parser actually returned.
        ranges[key] = (start, end)
    return ranges


def dump_section(key: str, value: object) -> str:
    """One top-level key rendered as YAML text, ending in a newline."""
    text = yaml.safe_dump(
        {key: value},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,
    )
    return text if text.endswith("\n") else text + "\n"


def splice(text: str, changes: dict[str, object]) -> str:
    """Replace the named top-level sections, leaving every other line untouched.

    A key set to ``None`` is removed. A key the document does not have yet is
    appended at the end, which is where a reader looking for what changed will
    find it.
    """
    if not changes:
        return text
    lines = text.splitlines(keepends=True)
    ranges = sections(text)

    # Highest line first, so replacing one section cannot move the next one's range.
    known = [(key, ranges[key]) for key in changes if key in ranges]
    for key, (start, end) in sorted(known, key=lambda item: item[1][0], reverse=True):
        value = changes[key]
        replacement = [] if value is None else [dump_section(key, value)]
        lines[start:end] = replacement

    appended = [
        dump_section(key, changes[key])
        for key in changes
        if key not in ranges and changes[key] is not None
    ]
    if appended:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.extend(appended)
    return "".join(lines)
