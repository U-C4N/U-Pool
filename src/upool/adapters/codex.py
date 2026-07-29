"""Codex CLI adapter - ``~/.codex/config.toml`` + ``~/.codex/auth.json``.

Codex points at a provider with a top-level ``model_provider`` key that names a
``[model_providers.<slug>]`` table. The API key itself is not stored in the TOML:
Codex reads it from the environment variable named by ``env_key``, and reads
``OPENAI_API_KEY`` out of ``auth.json`` as well - which is the only one U-Pool
can write on the user's behalf.

The TOML is edited with tomlkit so comments and key order survive a switch.
"""

from __future__ import annotations

from pathlib import Path

import tomlkit
from tomlkit import TOMLDocument

from .. import atomicio, backup, paths
from ..models import (
    APP_CODEX,
    WIRE_CHAT,
    WIRE_RESPONSES,
    Provider,
    UPoolError,
)
from .base import Adapter, ApplyResult

AUTH_ENV_KEY = "OPENAI_API_KEY"
PROVIDERS_KEY = "model_providers"
# Keys inside a provider table that U-Pool writes; anything else in the table
# came from the user's ``extra`` map and is written verbatim.
MANAGED_TABLE_KEYS = ("name", "base_url", "wire_api", "env_key")
# ``extra`` keys that belong at the root of config.toml (not under the provider table).
TOP_LEVEL_EXTRA_KEYS = frozenset(
    {
        "model_reasoning_effort",
        "disable_response_storage",
        "preferred_auth_method",
        "network_access",
        "review_model",
    }
)
BOOL_EXTRA_KEYS = frozenset(
    {
        "disable_response_storage",
        "requires_openai_auth",
    }
)

# Root-level knobs behind the advanced checkboxes. Written while the box is
# ticked, removed again when it is not - but only when the value on disk is the
# one U-Pool would have written, so a hand-picked policy is never overruled.
#
# Together these two are exactly what ``--dangerously-bypass-approvals-and-sandbox``
# sets. They are Codex-wide keys: inside ``[model_providers.<slug>]`` Codex would
# never see them.
APPROVAL_POLICY_KEY = "approval_policy"
SANDBOX_MODE_KEY = "sandbox_mode"
BYPASS_APPROVAL_POLICY = "never"
BYPASS_SANDBOX_MODE = "danger-full-access"
# Note the shape: ``[tools] web_search = true`` parses but is discarded by Codex.
# The switch that actually works is this top-level string.
WEB_SEARCH_KEY = "web_search"
WEB_SEARCH_LIVE = "live"
# Newer permission profiles supersede sandbox_mode and cannot be combined with it.
PERMISSIONS_PROFILE_KEY = "default_permissions"


def _coerce_extra_value(key: str, value: str):
    """TOML prefers real booleans for known flags; everything else stays a string."""
    if key in BOOL_EXTRA_KEYS:
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
    return value


class CodexAdapter(Adapter):
    app = APP_CODEX
    label = "Codex"

    def live_files(self) -> list[Path]:
        return [paths.codex_config_file(), paths.codex_auth_file()]

    def _read_config(self) -> TOMLDocument:
        config_path = paths.codex_config_file()
        if not config_path.exists():
            return tomlkit.document()
        try:
            return tomlkit.parse(config_path.read_text(encoding="utf-8"))
        except Exception as exc:  # tomlkit raises several parse error types
            raise UPoolError(
                f"{config_path} is not valid TOML, so it was left alone. Fix it and try again."
            ) from exc

    def _read_auth(self) -> dict:
        auth_path = paths.codex_auth_file()
        try:
            data = atomicio.read_json(auth_path, {})
        except ValueError as exc:
            raise UPoolError(f"{auth_path} is not valid JSON, so it was left alone.") from exc
        if not isinstance(data, dict):
            raise UPoolError(f"{auth_path} does not contain a JSON object.")
        return data

    def apply(self, provider: Provider, previous: Provider | None = None) -> ApplyResult:
        result = ApplyResult()
        doc = self._read_config()

        providers = doc.get(PROVIDERS_KEY)
        if providers is None:
            providers = tomlkit.table(is_super_table=True)
            doc[PROVIDERS_KEY] = providers

        # Retire the table we wrote for the provider we are switching away from,
        # so config.toml does not accumulate dead endpoints and stale keys.
        if previous is not None and not previous.official and previous.slug in providers:
            if previous.slug != provider.slug or provider.official:
                del providers[previous.slug]

        if provider.official:
            doc.pop("model_provider", None)
            if provider.model:
                doc["model"] = provider.model
            for key in TOP_LEVEL_EXTRA_KEYS:
                doc.pop(key, None)
        else:
            entry = tomlkit.table()
            entry["name"] = provider.name
            entry["base_url"] = provider.base_url.rstrip("/")
            entry["wire_api"] = provider.wire_api
            entry["env_key"] = provider.env_key
            for key, value in provider.extra.items():
                if value == "" or key in MANAGED_TABLE_KEYS:
                    continue
                if key in TOP_LEVEL_EXTRA_KEYS:
                    continue
                entry[key] = _coerce_extra_value(key, value)
            providers[provider.slug] = entry
            doc["model_provider"] = provider.slug
            if provider.model:
                doc["model"] = provider.model
            # Top-level Codex knobs (reasoning effort, storage, auth preference…).
            for key in TOP_LEVEL_EXTRA_KEYS:
                value = provider.extra.get(key, "")
                if value == "":
                    doc.pop(key, None)
                else:
                    doc[key] = _coerce_extra_value(key, value)

        self._apply_toggles(doc, provider, result)

        if len(providers) == 0:
            doc.pop(PROVIDERS_KEY, None)

        config_path = paths.codex_config_file()
        snap = backup.snapshot(self.app, config_path)
        if snap:
            result.backups.append(str(snap))
        atomicio.write_text(config_path, tomlkit.dumps(doc), secret=True)
        result.files.append(str(config_path))

        result.warnings.extend(self._apply_auth(provider, result))
        return result

    def _apply_toggles(self, doc: TOMLDocument, provider: Provider, result: ApplyResult) -> None:
        """Project the advanced checkboxes onto the root of config.toml."""
        bypass = provider.bypass_approvals and not provider.official
        if bypass:
            doc[APPROVAL_POLICY_KEY] = BYPASS_APPROVAL_POLICY
            doc[SANDBOX_MODE_KEY] = BYPASS_SANDBOX_MODE
        elif self._bypass_pair_present(doc):
            # Both halves together are U-Pool's own fingerprint - and the only shape
            # import_live reads back as "bypass on". A lone hand-written
            # sandbox_mode = "danger-full-access" is somebody else's setting: leave it.
            doc.pop(APPROVAL_POLICY_KEY, None)
            doc.pop(SANDBOX_MODE_KEY, None)
        self._root_flag(
            doc,
            WEB_SEARCH_KEY,
            WEB_SEARCH_LIVE,
            provider.web_search and not provider.official,
        )
        if bypass and PERMISSIONS_PROFILE_KEY in doc:
            result.warnings.append(
                f"config.toml already sets {PERMISSIONS_PROFILE_KEY}, which replaces "
                f"{SANDBOX_MODE_KEY} - Codex will not accept both."
            )

    @staticmethod
    def _bypass_pair_present(doc: TOMLDocument) -> bool:
        return (
            doc.get(APPROVAL_POLICY_KEY) == BYPASS_APPROVAL_POLICY
            and doc.get(SANDBOX_MODE_KEY) == BYPASS_SANDBOX_MODE
        )

    @staticmethod
    def _root_flag(doc: TOMLDocument, key: str, value: str, enabled: bool) -> None:
        """Own ``key`` while the box is ticked; give it back when it is not."""
        if enabled:
            doc[key] = value
        elif doc.get(key) == value:
            doc.pop(key, None)

    def _apply_auth(self, provider: Provider, result: ApplyResult) -> list[str]:
        """Write the key into auth.json, preserving any ChatGPT login tokens."""
        warnings: list[str] = []
        auth_path = paths.codex_auth_file()
        auth = self._read_auth()

        if provider.official or not provider.api_key:
            if AUTH_ENV_KEY not in auth:
                return warnings
            auth.pop(AUTH_ENV_KEY, None)
            if not provider.official:
                warnings.append("No API key set - Codex will fail to authenticate.")
        elif provider.env_key == AUTH_ENV_KEY:
            if auth.get(AUTH_ENV_KEY) == provider.api_key:
                return warnings
            auth[AUTH_ENV_KEY] = provider.api_key
        else:
            # Codex only ever reads OPENAI_API_KEY out of auth.json; a custom
            # env_key has to come from the user's own environment.
            return [
                f"This provider reads its key from ${provider.env_key}. "
                f"Set that variable yourself - only ${AUTH_ENV_KEY} can be written to auth.json."
            ]

        snap = backup.snapshot(self.app, auth_path)
        if snap:
            result.backups.append(str(snap))
        atomicio.write_json(auth_path, auth, secret=True)
        result.files.append(str(auth_path))
        return warnings

    def import_live(self) -> Provider | None:
        doc = self._read_config()
        slug = doc.get("model_provider")
        if not slug:
            return None
        table = (doc.get(PROVIDERS_KEY) or {}).get(str(slug))
        if table is None:
            return None
        env_key = str(table.get("env_key") or AUTH_ENV_KEY)
        api_key = ""
        if env_key == AUTH_ENV_KEY:
            api_key = str(self._read_auth().get(AUTH_ENV_KEY) or "")
        wire_api = str(table.get("wire_api") or WIRE_RESPONSES)
        extra = {
            str(k): str(v)
            for k, v in dict(table).items()
            if k not in MANAGED_TABLE_KEYS
        }
        for key in TOP_LEVEL_EXTRA_KEYS:
            if key in doc:
                extra[key] = str(doc[key]).lower() if isinstance(doc[key], bool) else str(doc[key])
        return Provider(
            app=APP_CODEX,
            name=str(table.get("name") or slug),
            note="Picked up from your existing config.toml on first run.",
            base_url=str(table.get("base_url") or ""),
            api_key=api_key,
            model=str(doc.get("model") or ""),
            wire_api=wire_api if wire_api in (WIRE_RESPONSES, WIRE_CHAT) else WIRE_RESPONSES,
            env_key=env_key,
            extra=extra,
            bypass_approvals=self._bypass_pair_present(doc),
            web_search=doc.get(WEB_SEARCH_KEY) == WEB_SEARCH_LIVE,
        )

    def health_target(self, provider: Provider) -> tuple[str, dict[str, str]] | None:
        if provider.official or not provider.base_url:
            return None
        url = provider.base_url.rstrip("/") + "/models"
        headers = {}
        if provider.api_key:
            headers["authorization"] = f"Bearer {provider.api_key}"
        return url, headers
