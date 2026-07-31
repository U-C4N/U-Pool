r"""Claude Desktop - present as a tab, deliberately inert as an adapter.

Claude Desktop has no base-URL setting to write. Its only lever is the OS
environment, and that lever is shared with Claude Code: the same ``ANTHROPIC_*``
names, one namespace between the two apps. Writing them from this tab would move
Claude Code's endpoint at the same time, and there is no second set of names to
keep the two apart - so 0.6.0 ships the tab and defers the write rather than
guessing at which app should win. Switching here still saves the provider and
records the choice, so nothing the user typed is lost when support lands.

``%APPDATA%\Claude\claude_desktop_config.json`` is never opened. That file is the
user's own - ``mcpServers`` and app preferences - and holds nothing a provider
switch has any business rewriting.
"""

from __future__ import annotations

from pathlib import Path

from ..models import APP_CLAUDE_DESKTOP, Provider
from .base import Adapter, ApplyResult
from .claude import ClaudeAdapter

PREVIEW_WARNING = (
    "Claude Desktop is preview-only in 0.6.0 - your choice was recorded, "
    "but no configuration was written."
)

# Both apps authenticate against the same endpoint with the same headers, so the
# Test button borrows Claude Code's probe instead of growing a second copy of it.
_CLAUDE_CODE = ClaudeAdapter()


class ClaudeDesktopAdapter(Adapter):
    app = APP_CLAUDE_DESKTOP
    label = "Claude Desktop"
    env_namespace = ""

    def live_files(self) -> list[Path]:
        return []

    def apply(self, provider: Provider) -> ApplyResult:
        return ApplyResult(warnings=[PREVIEW_WARNING])

    def import_live(self) -> Provider | None:
        return None

    def health_target(self, provider: Provider) -> tuple[str, dict[str, str]] | None:
        return _CLAUDE_CODE.health_target(provider)
