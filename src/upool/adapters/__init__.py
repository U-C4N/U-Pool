"""Registry of the CLIs U-Pool can switch."""

from __future__ import annotations

from ..models import APP_CLAUDE, APP_CLAUDE_DESKTOP, APP_CODEX, UPoolError
from .base import Adapter, ApplyResult
from .claude import ClaudeAdapter
from .claude_desktop import ClaudeDesktopAdapter
from .codex import CodexAdapter

# Insertion order is tab order: ``all_apps`` hands the UI this dict as it stands.
_ADAPTERS: dict[str, Adapter] = {
    APP_CLAUDE: ClaudeAdapter(),
    APP_CLAUDE_DESKTOP: ClaudeDesktopAdapter(),
    APP_CODEX: CodexAdapter(),
}


def get(app: str) -> Adapter:
    try:
        return _ADAPTERS[app]
    except KeyError:
        raise UPoolError(f"Unknown app '{app}'.") from None


def all_apps() -> list[dict[str, str]]:
    return [{"id": key, "label": adapter.label} for key, adapter in _ADAPTERS.items()]


__all__ = ["Adapter", "ApplyResult", "get", "all_apps"]
