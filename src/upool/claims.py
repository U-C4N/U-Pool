"""What U-Pool put where, so a later switch takes back exactly that and no more.

Two targets need the same discipline for the same reason. ``HKCU\\Environment``
keeps the user's own variables beside the ones a switch writes. So, it turns out,
does the ``env`` block of ``~/.claude/settings.json``: a user whose stored
credential had gone stale put ``ANTHROPIC_AUTH_TOKEN`` there by hand to stop
Claude Code refreshing it, and a block rebuilt from the provider record alone
deleted that on the next switch and broke the CLI a second time.

So a switch records the names it wrote, and the next one removes only those.
Anything sharing the same place is somebody else's and survives untouched. The
record is per namespace, because the registry and the settings file can hold the
same name for different reasons.

Kept out of ``settings.json``: this is a note of what was done to the machine,
not a preference. A preferences file copied in from another install must not be
able to tell U-Pool it owns values it never wrote.
"""

from __future__ import annotations

from . import atomicio, paths


def read(namespace: str) -> list[str]:
    """The names U-Pool set for ``namespace`` - the only ones it may remove."""
    return list(_load().get(namespace, []))


def write(namespace: str, names: list[str]) -> None:
    """Record the claim, dropping duplicates but keeping the order written."""
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        folded = name.lower()
        if folded in seen:
            continue
        seen.add(folded)
        unique.append(name)
    claims = _load()
    claims[namespace] = unique
    atomicio.write_json(paths.env_owned_file(), claims)


def _load() -> dict[str, list[str]]:
    try:
        raw = atomicio.read_json(paths.env_owned_file(), None)
    except Exception:  # noqa: BLE001 - a damaged record owns nothing, which is safe
        return {}
    if not isinstance(raw, dict):
        return {}
    claims: dict[str, list[str]] = {}
    for namespace, names in raw.items():
        if isinstance(names, list):
            claims[str(namespace)] = [str(n) for n in names if str(n).strip()]
    return claims
