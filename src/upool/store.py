"""Single source of truth for provider records.

``~/.u-pool/config.json`` holds every provider the user has defined. The live
files under ``~/.claude`` and ``~/.codex`` are treated as *output*: they always
show a projection of whichever provider is currently active. Nothing is ever
read back out of them except on first run, when an existing setup is imported
so it is not lost.
"""

from __future__ import annotations

import threading
from typing import Any

from . import adapters, atomicio, paths
from .adapters.base import ApplyResult
from .models import (
    APP_CLAUDE,
    APP_CODEX,
    SUPPORTED_APPS,
    Provider,
    UPoolError,
    now_ms,
    validate,
)

CONFIG_VERSION = 1

OFFICIAL_SEEDS = {
    APP_CLAUDE: {
        "name": "Claude Official",
        "website": "https://www.anthropic.com/claude-code",
        "note": "Sign in with your Anthropic account. Clears every managed env var.",
    },
    APP_CODEX: {
        "name": "OpenAI Official",
        "website": "https://developers.openai.com/codex",
        "note": "Sign in with your ChatGPT account. Clears the custom provider entry.",
    },
}


def _empty() -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "apps": {app: {"current": "", "providers": []} for app in SUPPORTED_APPS},
    }


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] | None = None

    # ---------------------------------------------------------------- loading

    def data(self) -> dict[str, Any]:
        with self._lock:
            if self._data is None:
                self._data = self._load()
            return self._data

    def _load(self) -> dict[str, Any]:
        config_path = paths.config_file()
        try:
            raw = atomicio.read_json(config_path, None)
        except ValueError as exc:
            raise UPoolError(
                f"{config_path} is not valid JSON. Move it aside to start fresh."
            ) from exc
        if raw is None:
            data = self._bootstrap()
            self._data = data
            self._save(data)
            return data
        if not isinstance(raw, dict):
            raise UPoolError(f"{config_path} does not contain a JSON object.")
        return self._normalise(raw)

    def _normalise(self, raw: dict[str, Any]) -> dict[str, Any]:
        data = _empty()
        data["version"] = int(raw.get("version") or CONFIG_VERSION)
        raw_apps = raw.get("apps") or {}
        for app in SUPPORTED_APPS:
            slot = raw_apps.get(app) or {}
            providers = [Provider.from_dict(p).to_dict() for p in slot.get("providers") or []]
            known = {p["id"] for p in providers}
            current = slot.get("current") or ""
            data["apps"][app] = {
                "current": current if current in known else "",
                "providers": providers,
            }
        return data

    def _bootstrap(self) -> dict[str, Any]:
        """First run: seed an official entry per app and import any live setup."""
        data = _empty()
        for app in SUPPORTED_APPS:
            seed = OFFICIAL_SEEDS[app]
            official = Provider(app=app, official=True, **seed)
            entries = [official.to_dict()]
            current = official.id
            try:
                imported = adapters.get(app).import_live()
            except UPoolError:
                # A broken live config must not block the app from starting.
                imported = None
            if imported is not None:
                entries.append(imported.to_dict())
                current = imported.id
            data["apps"][app] = {"current": current, "providers": entries}
        return data

    # ---------------------------------------------------------------- saving

    def _save(self, data: dict[str, Any] | None = None) -> None:
        atomicio.write_json(paths.config_file(), data if data is not None else self.data(), secret=True)

    # ---------------------------------------------------------------- reading

    def _slot(self, app: str) -> dict[str, Any]:
        if app not in SUPPORTED_APPS:
            raise UPoolError(f"Unknown app '{app}'.")
        return self.data()["apps"][app]

    def list_providers(self, app: str) -> list[Provider]:
        return [Provider.from_dict(p) for p in self._slot(app)["providers"]]

    def current_id(self, app: str) -> str:
        return self._slot(app)["current"]

    def get(self, app: str, provider_id: str) -> Provider:
        for provider in self.list_providers(app):
            if provider.id == provider_id:
                return provider
        raise UPoolError("That provider no longer exists.")

    def find(self, app: str, provider_id: str) -> Provider | None:
        try:
            return self.get(app, provider_id)
        except UPoolError:
            return None

    # ---------------------------------------------------------------- writing

    def add(self, provider: Provider) -> Provider:
        validate(provider)
        with self._lock:
            slot = self._slot(provider.app)
            if any(p["name"].strip().lower() == provider.name.strip().lower() for p in slot["providers"]):
                raise UPoolError(f"A provider named '{provider.name}' already exists.")
            provider.created_at = provider.updated_at = now_ms()
            slot["providers"].append(provider.to_dict())
            self._save()
        return provider

    def update(self, provider: Provider) -> Provider:
        validate(provider)
        with self._lock:
            slot = self._slot(provider.app)
            for index, existing in enumerate(slot["providers"]):
                if existing["id"] != provider.id:
                    continue
                clash = any(
                    p["id"] != provider.id
                    and p["name"].strip().lower() == provider.name.strip().lower()
                    for p in slot["providers"]
                )
                if clash:
                    raise UPoolError(f"A provider named '{provider.name}' already exists.")
                provider.created_at = existing.get("created_at", now_ms())
                provider.updated_at = now_ms()
                slot["providers"][index] = provider.to_dict()
                self._save()
                break
            else:
                raise UPoolError("That provider no longer exists.")
        # Editing the provider that is live has to reach the live files too,
        # otherwise the UI would show settings the CLI is not actually using.
        if self.current_id(provider.app) == provider.id:
            self.switch(provider.app, provider.id)
        return provider

    def duplicate(self, app: str, provider_id: str) -> Provider:
        source = self.get(app, provider_id)
        copy = Provider.from_dict(source.to_dict())
        copy.id = Provider().id
        copy.official = False
        base_name = f"{source.name} copy"
        name = base_name
        counter = 2
        existing = {p.name.strip().lower() for p in self.list_providers(app)}
        while name.strip().lower() in existing:
            name = f"{base_name} {counter}"
            counter += 1
        copy.name = name
        return self.add(copy)

    def delete(self, app: str, provider_id: str) -> None:
        with self._lock:
            slot = self._slot(app)
            remaining = [p for p in slot["providers"] if p["id"] != provider_id]
            if len(remaining) == len(slot["providers"]):
                raise UPoolError("That provider no longer exists.")
            if slot["current"] == provider_id:
                raise UPoolError("This provider is in use. Switch to another one first.")
            slot["providers"] = remaining
            self._save()

    def reorder(self, app: str, ordered_ids: list[str]) -> None:
        with self._lock:
            slot = self._slot(app)
            by_id = {p["id"]: p for p in slot["providers"]}
            if set(ordered_ids) != set(by_id):
                raise UPoolError("Reorder request did not match the current provider list.")
            slot["providers"] = [by_id[pid] for pid in ordered_ids]
            self._save()

    # ---------------------------------------------------------------- switching

    def switch(self, app: str, provider_id: str) -> ApplyResult:
        with self._lock:
            provider = self.get(app, provider_id)
            validate(provider)
            previous = self.find(app, self.current_id(app))
            if previous is not None and previous.id == provider.id:
                previous = None
            result = adapters.get(app).apply(provider, previous)
            self._slot(app)["current"] = provider.id
            self._save()
        return result
