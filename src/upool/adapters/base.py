"""Adapter contract.

An adapter is the only place that knows the on-disk shape of a target CLI's
config. It never decides *which* provider is active - the store owns that - it
only projects a provider record onto the live files, and reads them back.

That projection is a *clean write*: the live file is rebuilt from the provider
record alone. Merging into whatever was already there is what used to leave two
providers' settings layered in one file, so ``apply`` does not need - and no
longer takes - the provider being switched away from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..models import Provider

# The release in which switching started rewriting the live files whole. Both
# adapters keep one permanent pre-clean-write copy under this tag, so a user
# upgrading into that behaviour can always get their old file back.
CLEAN_WRITE_TAG = "0.5.0"


@dataclass
class ApplyResult:
    """What a switch actually did, so the UI can be specific about it."""

    files: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Keys that were in the live file and are not any more. Deliberately not a
    # warning: a switch rewrites the file from scratch, so this is the expected
    # outcome being reported, not a problem.
    removed: list[str] = field(default_factory=list)


class Adapter:
    app: str = ""
    label: str = ""

    def live_files(self) -> list[Path]:
        raise NotImplementedError

    def apply(self, provider: Provider) -> ApplyResult:
        """Make ``provider`` the active configuration for this app.

        Writes the live file from scratch: afterwards it holds exactly what
        ``provider`` describes and nothing else.
        """
        raise NotImplementedError

    def import_live(self) -> Provider | None:
        """Build a provider record from whatever is configured right now.

        Used once, on first run, so U-Pool never overwrites a working setup
        without keeping a copy of it in the provider list.
        """
        raise NotImplementedError

    def health_target(self, provider: Provider) -> tuple[str, dict[str, str]] | None:
        """URL and headers to probe for a connectivity test."""
        raise NotImplementedError
