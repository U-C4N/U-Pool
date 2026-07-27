"""Provider records.

One dataclass covers both target apps. Claude Code and Codex need different
fields, but keeping a single shape means the store, the JS bridge and the UI
all speak one language; each adapter reads only the fields it cares about.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

APP_CLAUDE = "claude"
APP_CODEX = "codex"
SUPPORTED_APPS = (APP_CLAUDE, APP_CODEX)

# Claude Code accepts either a bearer token or the classic x-api-key header;
# which one a relay wants is the single most common setup mistake, so it is an
# explicit field rather than a guess.
AUTH_TOKEN = "auth_token"
AUTH_API_KEY = "api_key"

# Codex talks either the OpenAI Responses API or the older chat/completions one.
WIRE_RESPONSES = "responses"
WIRE_CHAT = "chat"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str, fallback: str = "provider") -> str:
    """TOML table key for a provider name (``[model_providers.<slug>]``)."""
    slug = _SLUG_STRIP.sub("_", name.strip().lower()).strip("_")
    return slug or fallback


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Provider:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    app: str = APP_CLAUDE
    name: str = ""
    note: str = ""
    website: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""

    # Claude Code
    auth_style: str = AUTH_TOKEN
    small_fast_model: str = ""

    # Codex
    wire_api: str = WIRE_RESPONSES
    env_key: str = "OPENAI_API_KEY"

    # Free-form escape hatch: extra env vars (Claude) / extra provider keys (Codex).
    extra: dict[str, str] = field(default_factory=dict)

    # An "official" provider means: hand control back to the vendor's own login
    # flow by removing everything U-Pool manages from the live config.
    official: bool = False

    created_at: int = field(default_factory=now_ms)
    updated_at: int = field(default_factory=now_ms)

    @property
    def slug(self) -> str:
        return slugify(self.name, fallback=self.id[:8])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def redacted(self) -> dict[str, Any]:
        """Same record with the key masked, for logs and error messages."""
        data = self.to_dict()
        data["api_key"] = mask_secret(self.api_key)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Provider":
        known = {f for f in cls.__dataclass_fields__}  # noqa: SLF001 - dataclass API
        clean = {k: v for k, v in data.items() if k in known}
        clean.setdefault("id", uuid.uuid4().hex)
        extra = clean.get("extra") or {}
        clean["extra"] = {str(k): str(v) for k, v in dict(extra).items()}
        return cls(**clean)


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 6}{value[-4:]}"


class UPoolError(Exception):
    """Any failure that should surface to the UI as a readable message."""


def validate(provider: Provider) -> None:
    if provider.app not in SUPPORTED_APPS:
        raise UPoolError(f"Unknown app '{provider.app}'.")
    if not provider.name.strip():
        raise UPoolError("Provider name is required.")
    if provider.official:
        return
    if not provider.base_url.strip():
        raise UPoolError("Request URL is required.")
    if not provider.base_url.startswith(("http://", "https://")):
        raise UPoolError("Request URL must start with http:// or https://.")
    if provider.app == APP_CLAUDE and provider.auth_style not in (AUTH_TOKEN, AUTH_API_KEY):
        raise UPoolError(f"Unknown auth style '{provider.auth_style}'.")
    if provider.app == APP_CODEX:
        if provider.wire_api not in (WIRE_RESPONSES, WIRE_CHAT):
            raise UPoolError(f"Unknown wire API '{provider.wire_api}'.")
        if not provider.env_key.strip():
            raise UPoolError("Codex providers need an environment variable name for the key.")
