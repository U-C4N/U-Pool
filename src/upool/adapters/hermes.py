"""Hermes CLI adapter - ``config.yaml`` under the Hermes home.

Hermes keeps its providers in a top-level ``providers:`` mapping and picks one
with ``model.provider``; ``model.default`` names the model inside it. So a switch
is two edits: write the provider entry, then point ``model`` at it.

The ownership rule here is deliberately narrower than the Codex adapter's. Codex
deletes every ``[model_providers.*]`` table but the active one, because Codex was
reading two at once and the pile of dead tables was the bug. Hermes has no such
problem - it reads exactly the one ``model.provider`` names, and the rest of the
mapping is inert. A live config on this machine holds several entries the user
added by hand, so clearing the mapping would be data loss with nothing in the
provider record able to put it back. This adapter removes one entry: the one it
wrote last time, recorded in :mod:`upool.claims`.

The file is spliced rather than rewritten - see :mod:`upool.yamlio`. Hermes packs
thirty-odd unrelated sections into the same document (toolsets, kanban, skills,
the chat integrations and their tokens), and a switch has no business reformatting
any of them.

Nothing goes to the environment. Hermes resolves its key out of ``config.yaml``,
so ``env_namespace`` is empty and the registry is left alone.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .. import atomicio, backup, claims, paths, yamlio
from ..models import (
    APP_HERMES,
    TRANSPORT_ANTHROPIC,
    TRANSPORTS,
    Provider,
)
from .base import Adapter, ApplyResult

PROVIDERS_KEY = "providers"
MODEL_KEY = "model"
MODEL_PROVIDER_KEY = "provider"
MODEL_DEFAULT_KEY = "default"
# Which ``providers.<slug>`` entries U-Pool wrote. Everything else in the mapping
# is the user's, and a switch must be able to tell the difference after a restart.
PROVIDERS_CLAIM = "hermes_providers"
PROXY_MARKER_KEY = "upool_proxy"
MODEL_OVERRIDES_KEY = "model_overrides"
MODEL_OVERRIDES_CLAIM = "hermes_proxy_model_overrides"
PROXY_MODEL_CAPABILITIES = {
    "supports_reasoning": False,
    "supports_vision": False,
    "supports_tools": True,
}


def _explicit_reasoning(value: Any) -> bool:
    """Known Hermes effort forms that put a reasoning parameter on the wire."""
    if isinstance(value, dict):
        return value.get("enabled", True) is False or bool(str(value.get("effort") or "").strip())
    return value is False or isinstance(value, str) and value.strip().lower() in {
        "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "none", "false", "disabled",
    }
# Keys inside a provider entry that U-Pool writes; anything else there came from
# the ``extra`` map and is passed through untouched.
MANAGED_ENTRY_KEYS = ("name", "api", "api_key", "default_model", "models", "transport")


class HermesAdapter(Adapter):
    app = APP_HERMES
    label = "Hermes"
    env_namespace = ""

    def live_files(self) -> list[Path]:
        return [paths.hermes_config_file()]

    def apply(self, provider: Provider) -> ApplyResult:
        result = ApplyResult()
        config_path = paths.hermes_config_file()
        text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        # Parsing first means a file that will not read refuses the switch before
        # anything is written, the same way the Codex adapter refuses bad TOML.
        data = yamlio.read(config_path)

        providers = data.get(PROVIDERS_KEY)
        if not isinstance(providers, dict):
            providers = {}
        else:
            providers = dict(providers)
        model = data.get(MODEL_KEY)
        model = dict(model) if isinstance(model, dict) else {}

        kept = "" if provider.official else provider.slug
        result.removed = self._drop_claimed(providers, kept)

        if kept:
            providers[kept] = self._entry(provider, providers.get(kept))
            model[MODEL_PROVIDER_KEY] = kept
            if provider.model:
                model[MODEL_DEFAULT_KEY] = provider.model
            else:
                model.pop(MODEL_DEFAULT_KEY, None)
        else:
            # Handing control back: the entry is gone, so the selection that
            # pointed at it has to go too, or Hermes starts against a name that
            # is no longer there.
            if model.pop(MODEL_PROVIDER_KEY, None) is not None:
                result.removed.append(f"{MODEL_KEY}.{MODEL_PROVIDER_KEY}")
            if model.pop(MODEL_DEFAULT_KEY, None) is not None:
                result.removed.append(f"{MODEL_KEY}.{MODEL_DEFAULT_KEY}")

        claims.write(PROVIDERS_CLAIM, [kept] if kept else [])

        changes: dict[str, Any] = {
            MODEL_KEY: model if model else None,
            PROVIDERS_KEY: providers if providers else None,
        }
        changes.update(self._proxy_model_overrides(data, provider, result))
        updated = yamlio.splice(text, changes)
        if updated == text and config_path.exists():
            return result

        sidecar = backup.sidecar(config_path)
        if sidecar:
            result.backups.append(str(sidecar))
        atomicio.write_text(config_path, updated, secret=True)
        result.files.append(str(config_path))
        return result

    def _drop_claimed(self, providers: dict[str, Any], kept: str) -> list[str]:
        """Remove the entries U-Pool wrote before, and only those."""
        removed: list[str] = []
        for slug in claims.read(PROVIDERS_CLAIM):
            if slug and slug != kept and slug in providers:
                del providers[slug]
                removed.append(f"{PROVIDERS_KEY}.{slug}")
        return removed

    def _proxy_model_overrides(self, data: dict[str, Any], provider: Provider,
                               result: ApplyResult) -> dict[str, Any]:
        """Declare missing web-session capabilities without adopting user metadata.

        Hermes resolves named custom providers to the generic ``custom`` runtime.
        Its reasoning default reads this section, not the provider entry's own
        capabilities. Claims name individual fields; a later manual change gives
        that field back to the user even when switching to the official provider.
        """
        active = (not provider.official and provider.extra.get(PROXY_MARKER_KEY) == "chatgpt"
                  and provider.model.startswith("chatgpt/"))
        selected_model = provider.model if active else ""
        previous = data.get(MODEL_OVERRIDES_KEY, {})
        if not isinstance(previous, dict):
            claims.write(MODEL_OVERRIDES_CLAIM, [])
            if active:
                result.warnings.append("Hermes model_overrides is not a mapping; configure the ChatGPT model "
                                       "to omit reasoning parameters before using this API profile.")
            return {}
        overrides = deepcopy(previous)
        retained: list[str] = []
        for name in claims.read(MODEL_OVERRIDES_CLAIM):
            try:
                field = json.loads(name)
            except (TypeError, ValueError):
                continue
            if (not isinstance(field, list) or len(field) != 2
                    or not all(isinstance(part, str) for part in field)):
                continue
            model_name, key = field
            if key not in PROXY_MODEL_CAPABILITIES:
                continue
            custom = overrides.get("custom")
            entry = custom.get(model_name) if isinstance(custom, dict) else None
            if not isinstance(entry, dict) or entry.get(key) is not PROXY_MODEL_CAPABILITIES[key]:
                continue
            if model_name == selected_model:
                retained.append(name)
                continue
            del entry[key]
            result.removed.append(f"{MODEL_OVERRIDES_KEY}.custom.{model_name}.{key}")
            if not entry:
                custom.pop(model_name, None)
            if not custom:
                overrides.pop("custom", None)
        if active:
            custom = overrides.setdefault("custom", {})
            entry = custom.setdefault(selected_model, {}) if isinstance(custom, dict) else None
            if isinstance(entry, dict):
                for key, value in PROXY_MODEL_CAPABILITIES.items():
                    if key not in entry:
                        entry[key] = value
                        retained.append(json.dumps([selected_model, key], separators=(",", ":")))
                if entry.get("supports_reasoning") is True:
                    result.warnings.append("Hermes already marks this ChatGPT model as reasoning-capable. "
                                           "Set supports_reasoning to false and omit reasoning parameters "
                                           "before using this API profile; your setting was preserved.")
            else:
                result.warnings.append("Hermes has incompatible model_overrides for this ChatGPT model. "
                                       "Configure it to omit reasoning parameters before using this API profile; "
                                       "your settings were preserved.")
            agent = data.get("agent")
            effort = agent.get("reasoning_effort") if isinstance(agent, dict) else None
            if effort is not None and not (isinstance(effort, str) and not effort.strip()):
                result.warnings.append("Hermes agent.reasoning_effort is explicitly configured. "
                                       "Clear it so reasoning parameters are omitted for this ChatGPT API profile; "
                                       "your setting was preserved.")
            reasoning_overrides = agent.get("reasoning_overrides") if isinstance(agent, dict) else None
            if isinstance(reasoning_overrides, dict) and any(
                    _explicit_reasoning(reasoning_overrides.get(name))
                    for name in (selected_model, selected_model.split("/", 1)[1])):
                result.warnings.append("Hermes agent.reasoning_overrides explicitly configures this ChatGPT model. "
                                       "Clear its effort so reasoning parameters are omitted for this API profile; "
                                       "your setting was preserved.")
        claims.write(MODEL_OVERRIDES_CLAIM, retained)
        return {MODEL_OVERRIDES_KEY: overrides or None} if overrides != previous else {}

    def _entry(self, provider: Provider, existing: Any) -> dict[str, Any]:
        """The provider entry as Hermes wants it.

        Built from the record rather than merged into what is there: this is the
        one entry U-Pool owns whole, so a key it stops writing has to disappear.
        Keys the user added through ``extra`` are the exception and are re-applied
        on top.
        """
        transport = provider.transport if provider.transport in TRANSPORTS else TRANSPORT_ANTHROPIC
        entry: dict[str, Any] = {
            "name": provider.name,
            "api": provider.base_url.rstrip("/"),
            "api_key": provider.api_key,
            "transport": transport,
        }
        if provider.model:
            entry["default_model"] = provider.model
            entry["models"] = {provider.model: {"name": provider.model}}
        for key, value in provider.extra.items():
            if not key or key in MANAGED_ENTRY_KEYS or key == PROXY_MARKER_KEY:
                continue
            entry[key] = value
        # An entry that was there before may carry per-model metadata Hermes wrote
        # itself - context lengths, token limits. Only the models still named in
        # the record survive, but their details do.
        if isinstance(existing, dict) and provider.model:
            previous = existing.get("models")
            if isinstance(previous, dict) and isinstance(previous.get(provider.model), dict):
                entry["models"] = {provider.model: dict(previous[provider.model])}
                entry["models"][provider.model].setdefault("name", provider.model)
        return entry

    def import_live(self) -> Provider | None:
        data = yamlio.read(paths.hermes_config_file())
        model = data.get(MODEL_KEY)
        if not isinstance(model, dict):
            return None
        slug = str(model.get(MODEL_PROVIDER_KEY) or "")
        if not slug:
            return None
        providers = data.get(PROVIDERS_KEY)
        entry = providers.get(slug) if isinstance(providers, dict) else None
        if not isinstance(entry, dict):
            return None
        transport = str(entry.get("transport") or entry.get("api_mode") or TRANSPORT_ANTHROPIC)
        extra = {
            str(key): str(value)
            for key, value in entry.items()
            if key not in MANAGED_ENTRY_KEYS and not isinstance(value, (dict, list))
        }
        return Provider(
            app=APP_HERMES,
            name=str(entry.get("name") or slug),
            note="Picked up from your existing Hermes config.yaml on first run.",
            base_url=str(entry.get("api") or entry.get("base_url") or ""),
            api_key=str(entry.get("api_key") or ""),
            model=str(model.get(MODEL_DEFAULT_KEY) or entry.get("default_model") or ""),
            transport=transport if transport in TRANSPORTS else TRANSPORT_ANTHROPIC,
            extra=extra,
        )

    def health_target(self, provider: Provider) -> tuple[str, dict[str, str]] | None:
        if provider.official or not provider.base_url:
            return None
        base = provider.base_url.rstrip("/")
        headers: dict[str, str] = {}
        if provider.transport == TRANSPORT_ANTHROPIC:
            url = f"{base}/v1/models"
            if provider.api_key:
                headers["x-api-key"] = provider.api_key
                headers["anthropic-version"] = "2023-06-01"
        else:
            url = f"{base}/models"
            if provider.api_key:
                headers["authorization"] = f"Bearer {provider.api_key}"
        return url, headers
