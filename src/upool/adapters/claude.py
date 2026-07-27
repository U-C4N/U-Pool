"""Claude Code adapter - ``~/.claude/settings.json``.

Claude Code reads provider settings from the ``env`` block of its settings
file. Everything else in that file (permissions, hooks, statusLine, ...) is the
user's and is preserved untouched.
"""

from __future__ import annotations

from pathlib import Path

from .. import atomicio, backup, paths
from ..models import (
    APP_CLAUDE,
    AUTH_API_KEY,
    AUTH_TOKEN,
    Provider,
    UPoolError,
)
from .base import Adapter, ApplyResult

BASE_URL_KEY = "ANTHROPIC_BASE_URL"
AUTH_TOKEN_KEY = "ANTHROPIC_AUTH_TOKEN"
API_KEY_KEY = "ANTHROPIC_API_KEY"
MODEL_KEY = "ANTHROPIC_MODEL"
SMALL_FAST_MODEL_KEY = "ANTHROPIC_SMALL_FAST_MODEL"

# Keys U-Pool owns: they are cleared on every switch and rewritten from the
# provider record, so a stale token from a previous provider can never survive.
MANAGED_KEYS = (
    BASE_URL_KEY,
    AUTH_TOKEN_KEY,
    API_KEY_KEY,
    MODEL_KEY,
    SMALL_FAST_MODEL_KEY,
)


class ClaudeAdapter(Adapter):
    app = APP_CLAUDE
    label = "Claude Code"

    def live_files(self) -> list[Path]:
        return [paths.claude_settings_file()]

    def _read_settings(self) -> dict:
        settings_path = paths.claude_settings_file()
        try:
            data = atomicio.read_json(settings_path, {})
        except ValueError as exc:
            raise UPoolError(
                f"{settings_path} is not valid JSON, so it was left alone. Fix it and try again."
            ) from exc
        if not isinstance(data, dict):
            raise UPoolError(f"{settings_path} does not contain a JSON object.")
        return data

    def apply(self, provider: Provider, previous: Provider | None = None) -> ApplyResult:
        result = ApplyResult()
        settings_path = paths.claude_settings_file()
        settings = self._read_settings()

        env = dict(settings.get("env") or {})
        # Drop our own keys plus any custom ones the previous provider added,
        # otherwise switching would leave orphaned variables behind.
        stale = set(MANAGED_KEYS)
        if previous is not None:
            stale.update(previous.extra.keys())
        for key in stale:
            env.pop(key, None)

        if not provider.official:
            env[BASE_URL_KEY] = provider.base_url.rstrip("/")
            if provider.api_key:
                target = AUTH_TOKEN_KEY if provider.auth_style == AUTH_TOKEN else API_KEY_KEY
                env[target] = provider.api_key
            elif provider.auth_style in (AUTH_TOKEN, AUTH_API_KEY):
                result.warnings.append("No API key set - Claude Code will fail to authenticate.")
            if provider.model:
                env[MODEL_KEY] = provider.model
            if provider.small_fast_model:
                env[SMALL_FAST_MODEL_KEY] = provider.small_fast_model
            env.update({k: v for k, v in provider.extra.items() if v != ""})

        if env:
            settings["env"] = env
        else:
            settings.pop("env", None)

        snap = backup.snapshot(self.app, settings_path)
        if snap:
            result.backups.append(str(snap))
        atomicio.write_json(settings_path, settings, secret=True)
        result.files.append(str(settings_path))
        return result

    def import_live(self) -> Provider | None:
        settings = self._read_settings()
        env = settings.get("env") or {}
        base_url = str(env.get(BASE_URL_KEY, "")).strip()
        if not base_url:
            return None
        auth_style = AUTH_TOKEN if env.get(AUTH_TOKEN_KEY) else AUTH_API_KEY
        api_key = str(env.get(AUTH_TOKEN_KEY) or env.get(API_KEY_KEY) or "")
        extra = {
            str(k): str(v)
            for k, v in env.items()
            if k not in MANAGED_KEYS
        }
        return Provider(
            app=APP_CLAUDE,
            name="Imported",
            note="Picked up from your existing settings.json on first run.",
            base_url=base_url,
            api_key=api_key,
            auth_style=auth_style,
            model=str(env.get(MODEL_KEY, "")),
            small_fast_model=str(env.get(SMALL_FAST_MODEL_KEY, "")),
            extra=extra,
        )

    def health_target(self, provider: Provider) -> tuple[str, dict[str, str]] | None:
        if provider.official or not provider.base_url:
            return None
        url = provider.base_url.rstrip("/") + "/v1/models"
        headers = {"anthropic-version": "2023-06-01"}
        if provider.api_key:
            if provider.auth_style == AUTH_TOKEN:
                headers["authorization"] = f"Bearer {provider.api_key}"
            else:
                headers["x-api-key"] = provider.api_key
        return url, headers
