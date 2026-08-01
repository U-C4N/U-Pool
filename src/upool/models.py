"""Provider records.

One dataclass covers every target app. Claude Code and Codex need different
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
# Its own app id rather than a flavour of ``claude``: it gets its own tab, its
# own provider list and its own active choice. What it shares with Claude Code
# is the Anthropic environment variables, and those are keyed by namespace.
APP_CLAUDE_DESKTOP = "claude_desktop"
APP_CODEX = "codex"
APP_HERMES = "hermes"
APP_OPENCODE = "opencode"
# Order is tab order in the UI.
SUPPORTED_APPS = (APP_CLAUDE, APP_CLAUDE_DESKTOP, APP_CODEX, APP_HERMES, APP_OPENCODE)

# Claude Code accepts either a bearer token or the classic x-api-key header;
# which one a relay wants is the single most common setup mistake, so it is an
# explicit field rather than a guess.
AUTH_TOKEN = "auth_token"
AUTH_API_KEY = "api_key"

# Codex talks either the OpenAI Responses API or the older chat/completions one.
WIRE_RESPONSES = "responses"
WIRE_CHAT = "chat"

# Hermes names the wire protocol per provider, in its own vocabulary. The live
# config.yaml spells the key ``transport``; cc-switch calls the same idea
# ``api_mode``, and the values are shared between them.
TRANSPORT_ANTHROPIC = "anthropic_messages"
TRANSPORT_CHAT = "chat_completions"
TRANSPORT_RESPONSES = "codex_responses"
TRANSPORT_BEDROCK = "bedrock_converse"
TRANSPORTS = (TRANSPORT_ANTHROPIC, TRANSPORT_CHAT, TRANSPORT_RESPONSES, TRANSPORT_BEDROCK)

# OpenCode routes a provider through a Vercel AI SDK adapter, named by npm
# package. It is the one field that decides whether the endpoint is spoken to as
# Anthropic, as OpenAI or as something else, so it is a field rather than a guess.
NPM_ANTHROPIC = "@ai-sdk/anthropic"
NPM_OPENAI = "@ai-sdk/openai"
NPM_OPENAI_COMPATIBLE = "@ai-sdk/openai-compatible"
NPM_BEDROCK = "@ai-sdk/amazon-bedrock"
NPM_GOOGLE = "@ai-sdk/google"
NPM_PACKAGES = (
    NPM_ANTHROPIC,
    NPM_OPENAI,
    NPM_OPENAI_COMPATIBLE,
    NPM_BEDROCK,
    NPM_GOOGLE,
)

# Fields the UI sends as JSON booleans. A config.json hand-edited (or written by
# an older build) can hold "true" / 1 instead, so they are coerced on load.
BOOL_FIELDS = (
    "official",
    "bypass_permissions",
    "skip_bypass_prompt",
    "accept_edits",
    "all_project_mcp",
    "bypass_approvals",
    "web_search",
)

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

    # Hermes
    transport: str = TRANSPORT_ANTHROPIC

    # OpenCode
    npm: str = NPM_ANTHROPIC

    # Free-form escape hatch: extra env vars (Claude) / extra provider keys (Codex).
    extra: dict[str, str] = field(default_factory=dict)

    # Advanced toggles - one checkbox each in the form. Every flag maps onto a
    # single key in the target CLI's own config file; the adapters do the
    # projection. They live per provider on purpose: a scratch relay can run wide
    # open while the account you care about keeps every prompt.
    bypass_permissions: bool = False  # Claude: permissions.defaultMode
    skip_bypass_prompt: bool = False  # Claude: permissions.skipDangerousModePermissionPrompt
    accept_edits: bool = False  # Claude: permissions.defaultMode
    all_project_mcp: bool = False  # Claude: enableAllProjectMcpServers
    bypass_approvals: bool = False  # Codex: approval_policy + sandbox_mode
    web_search: bool = False  # Codex: web_search = "live"

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
        for key in BOOL_FIELDS:
            if key in clean:
                clean[key] = as_bool(clean[key])
        return cls(**clean)


def as_bool(value: Any) -> bool:
    """Truthiness with the strings a JSON/TOML round trip can produce."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


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
    # Claude Desktop takes the same record as Claude Code, so it takes the same check.
    claude_app = provider.app in (APP_CLAUDE, APP_CLAUDE_DESKTOP)
    if claude_app and provider.auth_style not in (AUTH_TOKEN, AUTH_API_KEY):
        raise UPoolError(f"Unknown auth style '{provider.auth_style}'.")
    if provider.app == APP_CODEX:
        if provider.wire_api not in (WIRE_RESPONSES, WIRE_CHAT):
            raise UPoolError(f"Unknown wire API '{provider.wire_api}'.")
        if not provider.env_key.strip():
            raise UPoolError("Codex providers need an environment variable name for the key.")
    if provider.app == APP_HERMES and provider.transport not in TRANSPORTS:
        raise UPoolError(f"Unknown Hermes transport '{provider.transport}'.")
    if provider.app == APP_OPENCODE:
        if provider.npm not in NPM_PACKAGES:
            raise UPoolError(f"Unknown OpenCode SDK package '{provider.npm}'.")
        if not provider.model.strip():
            # OpenCode selects a model with ``"<provider>/<model>"``; without the
            # second half there is nothing to write into the top-level key, and the
            # switch would define a provider without activating it.
            raise UPoolError("OpenCode providers need a model id.")
