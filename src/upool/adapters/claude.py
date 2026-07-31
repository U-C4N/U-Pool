"""Claude Code adapter - ``~/.claude/settings.json`` and the Anthropic variables.

U-Pool owns four things in this file: the ``env`` block, the two ``permissions``
keys behind the advanced checkboxes and ``enableAllProjectMcpServers``. Nothing
else. ``enabledPlugins``, ``extraKnownMarketplaces``, ``theme``, ``model``,
``effortLevel``, ``hooks``, ``statusLine`` and ``permissions.allow`` are Claude
Code's own state, and a provider record holds nothing that could recreate them.

Inside ``env`` the ownership is by name, not by block. The names in
:data:`MANAGED_KEYS` are unambiguously U-Pool's, and so are the pass-through keys
it wrote last time, which :mod:`upool.claims` remembers - between them that is
enough to clear out a previous provider without leaving its variables beside the
new one's, which was the original pile-up. Anything else in the block is somebody
else's: a user whose stored credential had gone stale put ``ANTHROPIC_AUTH_TOKEN``
here by hand to stop Claude Code refreshing it, and a block rebuilt from the
provider record alone deleted that and broke the CLI a second time. Switching to
a Claude provider still replaces the token, because that is what the switch means;
switching Codex, or editing an unrelated provider, now leaves it alone.

Which is why an unparsable settings.json now refuses the switch. Preserving what
we did not write means reading it first, and overwriting a file that would not
parse would throw away every key in it - the exact failure this adapter exists to
avoid.

The same values also go to ``HKCU\\Environment`` through the base class. Claude
Code prefers this file, but everything else that inherits the user's environment
- Claude Desktop, a shell, a script - only has the variables.
"""

from __future__ import annotations

from pathlib import Path

from .. import atomicio, backup, claims, paths, winenv
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

# The env vars U-Pool writes itself. Everything else in ``env`` comes from the
# provider's ``extra`` map; this tuple is what ``import_live`` uses to tell the
# two apart, and the only names it will look for in the registry - the user's own
# unrelated keys live in there too.
MANAGED_KEYS = (
    BASE_URL_KEY,
    AUTH_TOKEN_KEY,
    API_KEY_KEY,
    MODEL_KEY,
    SMALL_FAST_MODEL_KEY,
)

ENV_BLOCK_KEY = "env"
# Pass-through names written into the ``env`` block last time. Separate from the
# registry namespace: the same variable can be in both places for different
# reasons, and a claim on one is not a claim on the other.
SETTINGS_ENV_CLAIM = "claude_settings_env"
PERMISSIONS_KEY = "permissions"
DEFAULT_MODE_KEY = "defaultMode"
BYPASS_MODE = "bypassPermissions"
ACCEPT_EDITS_MODE = "acceptEdits"
PROJECT_MCP_KEY = "enableAllProjectMcpServers"
# Bypass mode otherwise opens with a one-off "do you accept the risk" dialog.
SKIP_BYPASS_PROMPT_KEY = "skipDangerousModePermissionPrompt"


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
    env_namespace = "anthropic"

    def live_files(self) -> list[Path]:
        return [paths.claude_settings_file()]

    def _read_settings(self) -> dict:
        """The live file as a dict - a parse failure is fatal on purpose.

        Everything the switch does not own is carried across from what this
        returns, so a file it cannot read is a file that cannot be written.
        """
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

    def env_vars(self, provider: Provider) -> dict[str, str]:
        """The Anthropic variables for a provider.

        One definition for two destinations: this is both the ``env`` block in
        settings.json and the registry namespace, which cannot be allowed to
        disagree about which provider is active.
        """
        if provider.official:
            return {}
        env = {BASE_URL_KEY: provider.base_url.rstrip("/")}
        if provider.api_key:
            target = AUTH_TOKEN_KEY if provider.auth_style == AUTH_TOKEN else API_KEY_KEY
            env[target] = provider.api_key
        if provider.model:
            env[MODEL_KEY] = provider.model
        if provider.small_fast_model:
            env[SMALL_FAST_MODEL_KEY] = provider.small_fast_model
        env.update({k: v for k, v in provider.extra.items() if v != ""})
        return env

    def apply(self, provider: Provider) -> ApplyResult:
        result = ApplyResult()
        settings_path = paths.claude_settings_file()
        settings = self._read_settings()

        env = self.env_vars(provider)
        needs_key = not provider.official and provider.auth_style in (AUTH_TOKEN, AUTH_API_KEY)
        if needs_key and not provider.api_key:
            result.warnings.append("No API key set - Claude Code will fail to authenticate.")

        old_env = settings.get(ENV_BLOCK_KEY)
        old_env = dict(old_env) if isinstance(old_env, dict) else {}
        merged, gone = self._merge_env(old_env, env)
        result.removed = [f"{ENV_BLOCK_KEY}.{name}" for name in gone]
        if merged:
            settings[ENV_BLOCK_KEY] = merged
        else:
            settings.pop(ENV_BLOCK_KEY, None)
        # Claimed after the merge decided them, and only the pass-through names:
        # MANAGED_KEYS are owned by being what they are and need no record.
        claims.write(SETTINGS_ENV_CLAIM, [name for name in env if name not in MANAGED_KEYS])

        result.removed.extend(self._apply_toggles(settings, provider))

        sidecar = backup.sidecar(settings_path)
        if sidecar:
            result.backups.append(str(sidecar))
        atomicio.write_json(settings_path, settings, secret=True)
        result.files.append(str(settings_path))

        self._apply_env(provider, result)
        return result

    @staticmethod
    def _merge_env(old_env: dict, wanted: dict[str, str]) -> tuple[dict, list[str]]:
        """The new ``env`` block, and the names that went away.

        Three groups. The provider's own variables go in. Names U-Pool is
        recognised as having written - :data:`MANAGED_KEYS`, plus whatever
        :mod:`upool.claims` remembers from last time - come out when this provider
        does not want them, so the previous relay leaves nothing behind. Everything
        else was put there by someone other than U-Pool and stays exactly as it is.
        """
        owned = {name.lower() for name in MANAGED_KEYS}
        owned.update(name.lower() for name in claims.read(SETTINGS_ENV_CLAIM))
        wanted_folded = {name.lower() for name in wanted}

        merged: dict = {}
        gone: list[str] = []
        for name, value in old_env.items():
            folded = str(name).lower()
            if folded in wanted_folded:
                # Rewritten below with the new value, under the new spelling.
                continue
            if folded in owned:
                gone.append(str(name))
                continue
            merged[name] = value
        merged.update(wanted)
        return merged, gone

    @staticmethod
    def _apply_toggles(settings: dict, provider: Provider) -> list[str]:
        """Set or delete the three keys behind the advanced checkboxes.

        Returns the names that went away. An unticked box has to actively delete
        its key now: the rest of the file survives the write, so a key nobody
        rewrites would otherwise stay ticked for good.
        """
        gone: list[str] = []
        block = settings.get(PERMISSIONS_KEY)
        if not isinstance(block, dict):
            # A scalar here is not something we can edit around, and it is not
            # ours to keep either, so it goes - reported, like any removal.
            if block is not None:
                gone.append(PERMISSIONS_KEY)
            block = {}

        mode = permission_mode(provider)
        if mode:
            block[DEFAULT_MODE_KEY] = mode
        elif block.pop(DEFAULT_MODE_KEY, None) is not None:
            gone.append(f"{PERMISSIONS_KEY}.{DEFAULT_MODE_KEY}")

        if provider.skip_bypass_prompt and not provider.official:
            block[SKIP_BYPASS_PROMPT_KEY] = True
        elif block.pop(SKIP_BYPASS_PROMPT_KEY, None) is not None:
            gone.append(f"{PERMISSIONS_KEY}.{SKIP_BYPASS_PROMPT_KEY}")

        if block:
            settings[PERMISSIONS_KEY] = block
        else:
            # An empty block is left behind by deleting the last key in it, and
            # it means nothing to Claude Code - drop it rather than leave litter.
            settings.pop(PERMISSIONS_KEY, None)

        if provider.all_project_mcp and not provider.official:
            settings[PROJECT_MCP_KEY] = True
        elif settings.pop(PROJECT_MCP_KEY, None) is not None:
            gone.append(PROJECT_MCP_KEY)
        return gone

    @staticmethod
    def _registry_env() -> dict[str, str]:
        """The managed Anthropic names as the Windows environment holds them.

        Only the names in :data:`MANAGED_KEYS`: the same registry key holds the
        user's own variables, and guessing which of those Claude Code cares about
        would import somebody's unrelated API key as a pass-through.
        """
        found: dict[str, str] = {}
        for name in MANAGED_KEYS:
            value = winenv.read(name)
            if value:
                found[name] = value
        return found

    def import_live(self) -> Provider | None:
        settings = self._read_settings()
        env = settings.get(ENV_BLOCK_KEY)
        env = dict(env) if isinstance(env, dict) else {}
        note = "Picked up from your existing settings.json on first run."
        if not env:
            # A setup can live entirely in the environment - the CLI reads it from
            # there just as happily - and that is what this author's machine looks
            # like. Without this, first run finds nothing and imports nothing.
            env = self._registry_env()
            note = "Picked up from your Windows environment variables on first run."

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
            note=note,
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
