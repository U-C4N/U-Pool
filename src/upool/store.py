"""Single source of truth for provider records.

``~/.u-pool/config.json`` holds every provider the user has defined; the live
files under ``~/.claude`` and ``~/.codex`` hold only the one that is active. The
store never touches those files itself - it hands the record to an adapter and
records what came back - and never reads them, except on first run, when an
existing setup is imported so it is not lost.
"""

from __future__ import annotations

import threading
from typing import Any

from . import adapters, atomicio, paths
from .adapters.base import ApplyResult
from .models import (
    APP_CLAUDE,
    APP_CLAUDE_DESKTOP,
    APP_CODEX,
    APP_HERMES,
    APP_OPENCODE,
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
    APP_CLAUDE_DESKTOP: {
        "name": "Claude Official",
        "website": "https://claude.ai/download",
        "note": "Sign in inside the Claude Desktop app. U-Pool does not write its config yet.",
    },
    APP_CODEX: {
        "name": "OpenAI Official",
        "website": "https://developers.openai.com/codex",
        "note": "Sign in with your ChatGPT account. Clears the custom provider entry.",
    },
    APP_HERMES: {
        "name": "Hermes Default",
        "website": "https://github.com/NousResearch/hermes",
        "note": "Clears the provider entry U-Pool wrote. Your own entries stay.",
    },
    APP_OPENCODE: {
        "name": "OpenCode Default",
        "website": "https://opencode.ai",
        "note": "Clears the provider entry U-Pool wrote and the model it selected.",
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
        self._seed_new_apps(data)
        return data

    def _seed_new_apps(self, data: dict[str, Any]) -> None:
        """Give an app that arrived after this config was written its first entry.

        ``_bootstrap`` only runs when there is no config file at all, so upgrading
        an install that already had one left the new tabs empty - and empty is not
        a neutral state here. The official entry is the only way to hand control
        back to the vendor, it cannot be created from the form, and a tab with no
        rows reads as a broken feature rather than a new one.

        Only ever *adds*, and only to a slot that has nothing in it: an app the
        user has emptied on purpose keeps whatever they left.
        """
        for app in SUPPORTED_APPS:
            slot = data["apps"][app]
            if slot["providers"]:
                continue
            official, current = self._first_entries(app)
            slot["providers"] = official
            slot["current"] = current

    def _first_entries(self, app: str) -> tuple[list[dict[str, Any]], str]:
        """The official entry, plus whatever is already configured live."""
        official = Provider(app=app, official=True, **OFFICIAL_SEEDS[app])
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
        return entries, current

    def _bootstrap(self) -> dict[str, Any]:
        """First run: seed an official entry per app and import any live setup."""
        data = _empty()
        for app in SUPPORTED_APPS:
            entries, current = self._first_entries(app)
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
            result = adapters.get(app).apply(provider)
            self._slot(app)["current"] = provider.id
            self._save()
        return result
