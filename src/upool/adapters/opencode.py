"""OpenCode adapter - ``~/.config/opencode/opencode.json``.

OpenCode declares providers under a ``provider`` object and selects one with a
top-level ``model`` string spelled ``"<provider>/<model>"``. Each provider names
the Vercel AI SDK package that speaks to it, which is why ``npm`` is a field on
the record rather than something this adapter could infer from the URL.

Two notes on what is owned here.

``model`` is written, and cc-switch - which this feature otherwise follows - does
not write it. It treats the key as the user's. But a switch that adds a provider
without selecting it has not switched anything, so U-Pool claims it. That is a
wider claim than the reference implementation makes, and it is the only one.

``provider`` entries are not cleared wholesale. Only the slug U-Pool wrote last
time is removed, recorded in :mod:`upool.claims`; a provider the user declared by
hand sits in the same object and survives. Everything else in the file -
``theme``, ``mcp``, ``plugin``, ``agent``, ``keybinds`` - is read, kept and
written back untouched.

Nothing goes to the environment: OpenCode reads its key from this file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import atomicio, backup, claims, paths
from ..models import (
    APP_OPENCODE,
    NPM_ANTHROPIC,
    NPM_PACKAGES,
    Provider,
    UPoolError,
)
from .base import Adapter, ApplyResult

SCHEMA_KEY = "$schema"
SCHEMA_URL = "https://opencode.ai/config.json"
PROVIDER_KEY = "provider"
MODEL_KEY = "model"
PROVIDERS_CLAIM = "opencode_providers"
# Keys inside a provider entry U-Pool writes. ``options`` is merged rather than
# replaced, because a relay may need headers this form does not ask for.
MANAGED_ENTRY_KEYS = ("npm", "name", "options", "models")
MANAGED_OPTION_KEYS = ("baseURL", "apiKey")


class OpenCodeAdapter(Adapter):
    app = APP_OPENCODE
    label = "OpenCode"
    env_namespace = ""

    def live_files(self) -> list[Path]:
        return [paths.opencode_config_file()]

    def _read(self) -> dict[str, Any]:
        config_path = paths.opencode_config_file()
        try:
            data = atomicio.read_json(config_path, {})
        except ValueError as exc:
            raise UPoolError(
                f"{config_path} is not valid JSON, so it was left alone. Fix it and try again."
            ) from exc
        if not isinstance(data, dict):
            raise UPoolError(f"{config_path} does not contain a JSON object.")
        return data

    def apply(self, provider: Provider) -> ApplyResult:
        result = ApplyResult()
        config_path = paths.opencode_config_file()
        data = self._read()
        before = dict(data)

        providers = data.get(PROVIDER_KEY)
        providers = dict(providers) if isinstance(providers, dict) else {}

        kept = "" if provider.official else provider.slug
        result.removed = self._drop_claimed(providers, kept)

        if kept:
            data[SCHEMA_KEY] = SCHEMA_URL
            providers[kept] = self._entry(provider, providers.get(kept))
            data[MODEL_KEY] = f"{kept}/{provider.model}"
        elif data.pop(MODEL_KEY, None) is not None:
            # No entry to point at any more, so the selection goes with it.
            result.removed.append(MODEL_KEY)

        if providers:
            data[PROVIDER_KEY] = providers
        elif data.pop(PROVIDER_KEY, None) is not None:
            result.removed.append(PROVIDER_KEY)

        claims.write(PROVIDERS_CLAIM, [kept] if kept else [])

        if data == before and config_path.exists():
            return result

        sidecar = backup.sidecar(config_path)
        if sidecar:
            result.backups.append(str(sidecar))
        atomicio.write_json(config_path, data, secret=True)
        result.files.append(str(config_path))
        return result

    def _drop_claimed(self, providers: dict[str, Any], kept: str) -> list[str]:
        removed: list[str] = []
        for slug in claims.read(PROVIDERS_CLAIM):
            if slug and slug != kept and slug in providers:
                del providers[slug]
                removed.append(f"{PROVIDER_KEY}.{slug}")
        return removed

    def _entry(self, provider: Provider, existing: Any) -> dict[str, Any]:
        npm = provider.npm if provider.npm in NPM_PACKAGES else NPM_ANTHROPIC
        options: dict[str, Any] = {}
        if isinstance(existing, dict) and isinstance(existing.get("options"), dict):
            # Headers and SDK flags a relay needs are set here and nowhere else in
            # the form, so they are kept; the two keys U-Pool owns are overwritten
            # below whatever they held.
            options = {
                key: value
                for key, value in existing["options"].items()
                if key not in MANAGED_OPTION_KEYS
            }
        options["baseURL"] = provider.base_url.rstrip("/")
        options["apiKey"] = provider.api_key

        entry: dict[str, Any] = {
            "npm": npm,
            "name": provider.name,
            "options": options,
            "models": {provider.model: {"name": provider.model}},
        }
        if isinstance(existing, dict):
            previous = existing.get("models")
            if isinstance(previous, dict) and isinstance(previous.get(provider.model), dict):
                # Context and output limits OpenCode filled in for this model are
                # worth more than the placeholder this adapter would write.
                kept_model = dict(previous[provider.model])
                kept_model.setdefault("name", provider.model)
                entry["models"] = {provider.model: kept_model}
        for key, value in provider.extra.items():
            if not key or key in MANAGED_ENTRY_KEYS:
                continue
            entry["options"][key] = value
        return entry

    def import_live(self) -> Provider | None:
        data = self._read()
        selection = str(data.get(MODEL_KEY) or "")
        if "/" not in selection:
            return None
        slug, _, model = selection.partition("/")
        providers = data.get(PROVIDER_KEY)
        entry = providers.get(slug) if isinstance(providers, dict) else None
        if not isinstance(entry, dict):
            return None
        options = entry.get("options")
        options = options if isinstance(options, dict) else {}
        npm = str(entry.get("npm") or NPM_ANTHROPIC)
        extra = {
            str(key): str(value)
            for key, value in options.items()
            if key not in MANAGED_OPTION_KEYS and not isinstance(value, (dict, list))
        }
        return Provider(
            app=APP_OPENCODE,
            name=str(entry.get("name") or slug),
            note="Picked up from your existing opencode.json on first run.",
            base_url=str(options.get("baseURL") or ""),
            api_key=str(options.get("apiKey") or ""),
            model=model,
            npm=npm if npm in NPM_PACKAGES else NPM_ANTHROPIC,
            extra=extra,
        )

    def health_target(self, provider: Provider) -> tuple[str, dict[str, str]] | None:
        if provider.official or not provider.base_url:
            return None
        base = provider.base_url.rstrip("/")
        headers: dict[str, str] = {}
        if provider.npm == NPM_ANTHROPIC:
            url = f"{base}/v1/models"
            if provider.api_key:
                headers["x-api-key"] = provider.api_key
                headers["anthropic-version"] = "2023-06-01"
        else:
            url = f"{base}/models"
            if provider.api_key:
                headers["authorization"] = f"Bearer {provider.api_key}"
        return url, headers
