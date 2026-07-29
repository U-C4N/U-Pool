"""Claude Code adapter - ``~/.claude/settings.json``.

Claude Code reads provider settings from the ``env`` block of its settings
file. Everything else in that file (hooks, statusLine, the permission
allow/deny lists, ...) is the user's and is preserved untouched.

Three keys are exceptions, each behind an explicit checkbox in the provider form:
``permissions.defaultMode``, ``permissions.skipDangerousModePermissionPrompt`` and
``enableAllProjectMcpServers``. While a box is ticked U-Pool owns that key;
unticking it takes the key back out again.
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

PERMISSIONS_KEY = "permissions"
DEFAULT_MODE_KEY = "defaultMode"
BYPASS_MODE = "bypassPermissions"
ACCEPT_EDITS_MODE = "acceptEdits"
PROJECT_MCP_KEY = "enableAllProjectMcpServers"
DISABLE_BYPASS_KEY = "disableBypassPermissionsMode"
# Bypass mode otherwise opens with a one-off "do you accept the risk" dialog.
SKIP_BYPASS_PROMPT_KEY = "skipDangerousModePermissionPrompt"
# The only two modes U-Pool ever writes. Anything else in ``defaultMode``
# ("plan", "default", a typo) was put there by hand and is left alone.
MANAGED_MODES = (BYPASS_MODE, ACCEPT_EDITS_MODE)


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

        self._apply_toggles(settings, provider, result)

        snap = backup.snapshot(self.app, settings_path)
        if snap:
            result.backups.append(str(snap))
        atomicio.write_json(settings_path, settings, secret=True)
        result.files.append(str(settings_path))
        return result

    def _apply_toggles(self, settings: dict, provider: Provider, result: ApplyResult) -> None:
        """Project the advanced checkboxes onto settings.json.

        Inside the permissions block only the two keys behind a checkbox are
        touched - the allow/deny/ask lists and everything else in there stay
        exactly as the user left them, and the block is only rewritten at all if
        something in it actually changed.
        """
        mode = permission_mode(provider)
        raw = settings.get(PERMISSIONS_KEY)
        if raw is not None and not isinstance(raw, dict):
            # Same rule as a malformed settings.json: do not touch a shape we do not
            # understand. Say so rather than replacing whatever is in there.
            result.warnings.append(
                f"'{PERMISSIONS_KEY}' in settings.json is not an object, so the permission "
                "switches were skipped."
            )
            self._apply_project_mcp(settings, provider)
            return
        original = raw or {}
        block = dict(original)

        if mode:
            block[DEFAULT_MODE_KEY] = mode
        elif block.get(DEFAULT_MODE_KEY) in MANAGED_MODES:
            # A mode we could have written, and nothing wants it now.
            block.pop(DEFAULT_MODE_KEY, None)

        # Owned by its own checkbox, not by the mode: Claude Code ignores it outside
        # bypass mode, so removing it there would drop a key that is still ticked.
        if provider.skip_bypass_prompt and not provider.official:
            block[SKIP_BYPASS_PROMPT_KEY] = True
        elif block.get(SKIP_BYPASS_PROMPT_KEY) is True:
            block.pop(SKIP_BYPASS_PROMPT_KEY, None)

        if block != original:
            if block:
                settings[PERMISSIONS_KEY] = block
            else:
                settings.pop(PERMISSIONS_KEY, None)

        if mode == BYPASS_MODE and block.get(DISABLE_BYPASS_KEY) == "disable":
            result.warnings.append(
                f"settings.json sets {DISABLE_BYPASS_KEY}, so Claude Code will refuse to "
                "start in bypass mode."
            )

        self._apply_project_mcp(settings, provider)

    @staticmethod
    def _apply_project_mcp(settings: dict, provider: Provider) -> None:
        if provider.all_project_mcp and not provider.official:
            settings[PROJECT_MCP_KEY] = True
        elif settings.get(PROJECT_MCP_KEY) is True:
            settings.pop(PROJECT_MCP_KEY, None)

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
