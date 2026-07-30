"""Claude Code adapter - ``~/.claude/settings.json``.

U-Pool owns this file outright. Every switch writes it from scratch from the
provider record: the ``env`` block Claude Code reads its endpoint and key from,
plus the three keys behind the advanced checkboxes
(``permissions.defaultMode``, ``permissions.skipDangerousModePermissionPrompt``
and ``enableAllProjectMcpServers``). Anything else that was in the file is gone.

That is the point. Merging - keeping the keys we did not recognise - is what left
a previous provider's variables sitting next to the new one's, which Claude Code
then read as one contradictory configuration. The names that were dropped come
back on :class:`~upool.adapters.base.ApplyResult` so the UI can say so, and
``backup`` keeps a copy of the file as it was.
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
from .base import CLEAN_WRITE_TAG, Adapter, ApplyResult

BASE_URL_KEY = "ANTHROPIC_BASE_URL"
AUTH_TOKEN_KEY = "ANTHROPIC_AUTH_TOKEN"
API_KEY_KEY = "ANTHROPIC_API_KEY"
MODEL_KEY = "ANTHROPIC_MODEL"
SMALL_FAST_MODEL_KEY = "ANTHROPIC_SMALL_FAST_MODEL"

# The env vars U-Pool writes itself. Everything else in ``env`` comes from the
# provider's ``extra`` map; this tuple is what ``import_live`` uses to tell the
# two apart when reading an existing setup back in.
MANAGED_KEYS = (
    BASE_URL_KEY,
    AUTH_TOKEN_KEY,
    API_KEY_KEY,
    MODEL_KEY,
    SMALL_FAST_MODEL_KEY,
)

PERMISSIONS_KEY = "permissions"
DEFAULT_MODE_KEY = "defaultMode"
BYPASS_MODE = "bypassPermissions"
ACCEPT_EDITS_MODE = "acceptEdits"
PROJECT_MCP_KEY = "enableAllProjectMcpServers"
# Bypass mode otherwise opens with a one-off "do you accept the risk" dialog.
SKIP_BYPASS_PROMPT_KEY = "skipDangerousModePermissionPrompt"

# The keys a clean write can produce. Anything else found in the file on the way
# out is reported as removed.
OWNED_KEYS = ("env", PERMISSIONS_KEY, PROJECT_MCP_KEY)


def permission_mode(provider: Provider) -> str:
    """``defaultMode`` for a provider, or ``""`` when neither box is ticked.

    Bypass wins over accept-edits: it is the wider of the two, so a provider with
    both boxes ticked gets the mode the user asked for rather than the safer one
    they also happened to leave on.
    """
    if provider.official:
        return ""
    if provider.bypass_permissions:
        return BYPASS_MODE
    if provider.accept_edits:
        return ACCEPT_EDITS_MODE
    return ""


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

    def apply(self, provider: Provider) -> ApplyResult:
        result = ApplyResult()
        settings_path = paths.claude_settings_file()
        settings: dict = {}

        if not provider.official:
            env = {BASE_URL_KEY: provider.base_url.rstrip("/")}
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
            settings["env"] = env

        self._apply_toggles(settings, provider)
        result.removed = self._removed(settings_path, settings)

        archive = backup.archive_once(self.app, settings_path, CLEAN_WRITE_TAG)
        if archive:
            result.backups.append(str(archive))
        snap = backup.snapshot(self.app, settings_path)
        if snap:
            result.backups.append(str(snap))
        atomicio.write_json(settings_path, settings, secret=True)
        result.files.append(str(settings_path))
        return result

    @staticmethod
    def _apply_toggles(settings: dict, provider: Provider) -> None:
        """Project the advanced checkboxes onto a settings dict being built.

        Nothing needs taking back out: an unticked box simply never writes its
        key, because the dict starts empty.
        """
        if provider.official:
            return
        block = {}
        mode = permission_mode(provider)
        if mode:
            block[DEFAULT_MODE_KEY] = mode
        if provider.skip_bypass_prompt:
            block[SKIP_BYPASS_PROMPT_KEY] = True
        if block:
            settings[PERMISSIONS_KEY] = block
        if provider.all_project_mcp:
            settings[PROJECT_MCP_KEY] = True

    def _removed(self, settings_path: Path, written: dict) -> list[str]:
        """Names present in the file now that the clean write will not put back.

        Best-effort by design: this only feeds a message. A file we cannot parse
        is no longer a reason to refuse the switch - nothing in it was going to
        be preserved anyway - so a parse failure just means no message.
        """
        try:
            before = self._read_settings()
        except UPoolError:
            return []
        gone = [key for key in before if key not in OWNED_KEYS]
        old_env = before.get("env")
        if isinstance(old_env, dict):
            new_env = written.get("env") or {}
            gone.extend(f"env.{name}" for name in old_env if name not in new_env)
        old_perms = before.get(PERMISSIONS_KEY)
        if isinstance(old_perms, dict):
            new_perms = written.get(PERMISSIONS_KEY) or {}
            gone.extend(f"{PERMISSIONS_KEY}.{name}" for name in old_perms if name not in new_perms)
        elif old_perms is not None and PERMISSIONS_KEY not in written:
            gone.append(PERMISSIONS_KEY)
        if before.get(PROJECT_MCP_KEY) is not None and PROJECT_MCP_KEY not in written:
            gone.append(PROJECT_MCP_KEY)
        return gone

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
        permissions = settings.get(PERMISSIONS_KEY)
        mode = permissions.get(DEFAULT_MODE_KEY) if isinstance(permissions, dict) else None
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
            bypass_permissions=mode == BYPASS_MODE,
            skip_bypass_prompt=(
                isinstance(permissions, dict) and permissions.get(SKIP_BYPASS_PROMPT_KEY) is True
            ),
            accept_edits=mode == ACCEPT_EDITS_MODE,
            all_project_mcp=settings.get(PROJECT_MCP_KEY) is True,
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
