"""Adapter contract.

An adapter is the only place that knows the on-disk shape of a target CLI's
config. It never decides *which* provider is active - the store owns that - it
only projects a provider record onto the live files, and reads them back.

That projection is a *surgical* write, not a clean one. The clean write was the
right answer to the wrong question: two providers can only end up layered in one
file if the *provider* keys are merged, and the way to stop that is to own those
keys whole - the ``env`` block, the ``[model_providers]`` tables - rather than to
own the file. Everything else in these files is the CLI's own state, not
U-Pool's: plugins and marketplaces, the theme, MCP servers, the per-project trust
levels a user answered a prompt for. A switch that deletes them is data loss, and
the record being switched to does not contain a single thing that could put them
back. So an adapter reads the file, replaces what it owns and writes the rest
back untouched.

The files are not the only target. The CLIs also resolve their endpoint and key
through the process environment, so :meth:`Adapter._apply_env` projects the same
record onto ``HKCU\\Environment`` via :mod:`upool.winenv` - owned by namespace
there, and just as whole.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import backup, winenv
from ..models import Provider


@dataclass
class ApplyResult:
    """What a switch actually did, so the UI can be specific about it."""

    files: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Things U-Pool owned that are not in the live file any more. Deliberately
    # not a warning: an owned key going away is the expected outcome being
    # reported, not a problem. A key U-Pool does not own never appears here,
    # because a surgical write does not touch it.
    removed: list[str] = field(default_factory=list)
    env_written: list[str] = field(default_factory=list)
    env_removed: list[str] = field(default_factory=list)


class Adapter:
    app: str = ""
    label: str = ""
    # Which set of environment variables this app owns - ``"anthropic"`` or
    # ``"openai"``. Empty means the app has no environment side at all, and
    # ``_apply_env`` becomes a no-op for it.
    env_namespace: str = ""

    def live_files(self) -> list[Path]:
        raise NotImplementedError

    def env_vars(self, provider: Provider) -> dict[str, str]:
        """The whole of what ``env_namespace`` should hold for ``provider``.

        Whole, because ownership is what makes removal safe: a name this returns
        is claimed, and a name it stops returning is taken back out. An official
        provider yields ``{}`` and so clears the namespace.
        """
        return {}

    def apply(self, provider: Provider) -> ApplyResult:
        """Make ``provider`` the active configuration for this app.

        Rewrites only the keys this adapter owns. Everything else in the live
        file is read, kept and written back as it was found.
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

    def _apply_env(self, provider: Provider, result: ApplyResult) -> None:
        """Project ``provider`` onto this app's environment namespace.

        Shared rather than duplicated per adapter: the two that have a namespace
        differ only in which names they return from :meth:`env_vars`.
        """
        if not self.env_namespace:
            return
        desired = self.env_vars(provider)
        plan = winenv.preview(self.env_namespace, desired)
        if plan and winenv.supported():
            # The snapshot holds the values as they stand *now*, not the plan:
            # what someone wants back after a switch they regret is the variable
            # they had, and ``None`` records a name that was unset, so undoing it
            # by hand is a deletion rather than an empty string.
            before: dict[str, str | None] = {name: winenv.read(name) for name in plan}
            snapshot = backup.env_snapshot(before)
            if snapshot:
                result.backups.append(str(snapshot))
        outcome = winenv.apply(self.env_namespace, desired)
        result.env_written = list(outcome.written)
        result.env_removed = list(outcome.removed)
        if outcome.error:
            result.warnings.append(
                f"Some Windows environment variables did not update - {outcome.error}"
            )
