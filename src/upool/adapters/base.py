"""Adapter contract.

An adapter is the only place that knows the on-disk shape of a target CLI's
config. It never decides *which* provider is active - the store owns that - it
only projects a provider record onto the live files, and reads them back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..models import Provider


@dataclass
class ApplyResult:
    """What a switch actually did, so the UI can be specific about it."""

    files: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Adapter:
    app: str = ""
    label: str = ""

    def live_files(self) -> list[Path]:
        raise NotImplementedError

    def apply(self, provider: Provider, previous: Provider | None = None) -> ApplyResult:
        """Make ``provider`` the active configuration for this app."""
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
