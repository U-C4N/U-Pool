"""Codex CLI adapter - ``~/.codex/config.toml`` + ``~/.codex/auth.json``.

Codex selects a provider with a top-level ``model_provider`` key naming a
``[model_providers.<slug>]`` table, and reads the key itself out of the process
environment under whatever name ``env_key`` gives. That second half is why a
switch that only rewrites files leaves every new shell on the previous
provider - :mod:`upool.winenv` is the other target now, and this adapter only
says which names belong to it.

``config.toml`` is edited in place. Writing it from scratch, which is what 0.5.0
did, threw away ``[mcp_servers.*]``, ``[projects.*]`` trust levels, ``notify``,
``[windows]`` and everything else a user builds up around the provider block, so
the write here touches the handful of keys U-Pool owns and leaves the document -
its ordering, its comments, its unrelated tables - exactly as it found it. A file
that will not parse refuses the write outright: nothing can be preserved out of a
document that could not be read.

``auth.json`` is the opposite, and is written from scratch every time. It is
where an older build left ``{"codefast": "..."}`` behind, and a name nothing ever
removes is precisely the failure a merge cannot fix. The one thing in there
U-Pool cannot recreate is a ChatGPT login, so the original is copied to
``~/.u-pool/codex-login.json`` before it goes and restored verbatim when the
official provider wins.
"""

from __future__ import annotations

from pathlib import Path

import tomlkit
from tomlkit import TOMLDocument

from .. import atomicio, backup, paths, winenv
from ..models import (
    APP_CODEX,
    WIRE_CHAT,
    WIRE_RESPONSES,
    Provider,
    UPoolError,
)
from .base import Adapter, ApplyResult

AUTH_ENV_KEY = "OPENAI_API_KEY"
BASE_URL_ENV_KEY = "OPENAI_BASE_URL"
PROVIDERS_KEY = "model_providers"
SELECTED_KEY = "model_provider"
MODEL_KEY = "model"
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

# Root-level knobs behind the advanced checkboxes.
#
# Together these two are exactly what ``--dangerously-bypass-approvals-and-sandbox``
# sets. They are Codex-wide keys: inside ``[model_providers.<slug>]`` Codex would
# never see them.
APPROVAL_POLICY_KEY = "approval_policy"
SANDBOX_MODE_KEY = "sandbox_mode"
BYPASS_APPROVAL_POLICY = "never"
BYPASS_SANDBOX_MODE = "danger-full-access"
# The newer spelling of the same idea. Codex refuses to start with this and
# ``sandbox_mode`` both set, so it is the one key outside U-Pool's own that a
# surgical write still removes - and only when it would be writing the pair that
# contradicts it.
CONFLICTING_SANDBOX_KEY = "default_permissions"
# Note the shape: ``[tools] web_search = true`` parses but is discarded by Codex.
# The switch that actually works is this top-level string.
WEB_SEARCH_KEY = "web_search"
WEB_SEARCH_LIVE = "live"


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
    env_namespace = "openai"

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

    def apply(self, provider: Provider) -> ApplyResult:
        result = ApplyResult()
        config_path = paths.codex_config_file()
        # Both files are read before either is written, so a switch that is going
        # to refuse over an unreadable file refuses with nothing half done.
        doc = self._read_config()
        auth = self._read_auth()

        result.removed = self._apply_root_keys(doc, provider)
        result.removed.extend(self._apply_provider_table(doc, provider))

        sidecar = backup.sidecar(config_path)
        if sidecar:
            result.backups.append(str(sidecar))
        atomicio.write_text(config_path, tomlkit.dumps(doc), secret=True)
        result.files.append(str(config_path))

        result.warnings.extend(self._apply_auth(provider, auth, result))
        self._apply_env(provider, result)
        return result

    def env_vars(self, provider: Provider) -> dict[str, str]:
        """The registry names that carry this provider to a freshly launched Codex.

        ``env_key`` is whatever the provider table names - ``codefast`` as often as
        ``OPENAI_API_KEY`` - because that is the name Codex resolves against the
        environment.
        """
        if provider.official:
            return {}
        values: dict[str, str] = {}
        if provider.api_key:
            values[provider.env_key or AUTH_ENV_KEY] = provider.api_key
        if provider.base_url:
            values[BASE_URL_ENV_KEY] = provider.base_url.rstrip("/")
        return values

    def _apply_root_keys(self, doc: TOMLDocument, provider: Provider) -> list[str]:
        """Set or clear the root keys U-Pool owns, reporting the ones that went.

        Every other root key and table in the document is somebody else's and is
        not read, written or counted here.
        """
        removed: list[str] = []

        def drop(key: str) -> None:
            if key in doc:
                del doc[key]
                removed.append(key)

        def put(key: str, value) -> None:
            # Assigning an identical value would re-render the line and take its
            # trailing comment with it.
            if doc.get(key) != value:
                doc[key] = value

        if provider.official:
            drop(SELECTED_KEY)
        else:
            put(SELECTED_KEY, provider.slug)

        if provider.model:
            put(MODEL_KEY, provider.model)
        else:
            drop(MODEL_KEY)

        # Sorted so a fresh file comes out the same way twice: set iteration order
        # is not stable across processes.
        for key in sorted(TOP_LEVEL_EXTRA_KEYS):
            value = "" if provider.official else provider.extra.get(key, "")
            if value == "":
                drop(key)
            else:
                put(key, _coerce_extra_value(key, value))

        if not provider.official and provider.bypass_approvals:
            put(APPROVAL_POLICY_KEY, BYPASS_APPROVAL_POLICY)
            put(SANDBOX_MODE_KEY, BYPASS_SANDBOX_MODE)
            drop(CONFLICTING_SANDBOX_KEY)
        else:
            drop(APPROVAL_POLICY_KEY)
            drop(SANDBOX_MODE_KEY)

        if not provider.official and provider.web_search:
            put(WEB_SEARCH_KEY, WEB_SEARCH_LIVE)
        else:
            drop(WEB_SEARCH_KEY)

        return removed

    def _apply_provider_table(self, doc: TOMLDocument, provider: Provider) -> list[str]:
        """Leave exactly one ``[model_providers.*]`` table behind - the active one.

        The pile of dead tables is what let Codex read two providers at once, and
        clearing it is the one place a surgical write is still ruthless.
        """
        removed: list[str] = []
        container = doc.get(PROVIDERS_KEY)
        if container is not None and not isinstance(container, dict):
            del doc[PROVIDERS_KEY]
            removed.append(PROVIDERS_KEY)
            container = None

        kept = "" if provider.official else provider.slug
        if container is not None:
            for slug in [str(key) for key in container]:
                if slug != kept:
                    del container[slug]
                    removed.append(f"{PROVIDERS_KEY}.{slug}")

        if provider.official:
            if container is not None:
                del doc[PROVIDERS_KEY]
            return removed

        if container is None:
            container = tomlkit.table(is_super_table=True)
            doc[PROVIDERS_KEY] = container
        entry = container.get(kept)
        if not isinstance(entry, dict):
            entry = tomlkit.table()
            container[kept] = entry

        wanted: dict[str, object] = {
            "name": provider.name,
            "base_url": provider.base_url.rstrip("/"),
            "wire_api": provider.wire_api,
            "env_key": provider.env_key,
        }
        for key, value in provider.extra.items():
            if value == "" or key in MANAGED_TABLE_KEYS or key in TOP_LEVEL_EXTRA_KEYS:
                continue
            wanted[key] = _coerce_extra_value(key, value)

        for key in [str(key) for key in entry]:
            if key not in wanted:
                del entry[key]
                removed.append(f"{PROVIDERS_KEY}.{kept}.{key}")
        for key, value in wanted.items():
            if entry.get(key) != value:
                entry[key] = value
        return removed

    @staticmethod
    def _bypass_pair_present(doc: TOMLDocument) -> bool:
        return (
            doc.get(APPROVAL_POLICY_KEY) == BYPASS_APPROVAL_POLICY
            and doc.get(SANDBOX_MODE_KEY) == BYPASS_SANDBOX_MODE
        )

    def _apply_auth(self, provider: Provider, auth: dict, result: ApplyResult) -> list[str]:
        """Write auth.json from scratch, keeping the ChatGPT login recoverable."""
        warnings: list[str] = []
        auth_path = paths.codex_auth_file()
        self._stash_login(auth)

        if provider.official:
            desired = self._stashed_login()
        elif not provider.api_key:
            desired = {}
            warnings.append("No API key set - Codex will fail to authenticate.")
        elif provider.env_key == AUTH_ENV_KEY:
            desired = {AUTH_ENV_KEY: provider.api_key}
        else:
            # Codex only ever reads OPENAI_API_KEY out of this file. A provider on
            # any other name is served by the registry instead, so there is
            # nothing to put here.
            desired = {}

        if desired == auth:
            # Also the path where there is no file and nothing to write: an empty
            # auth.json is worse than none.
            return warnings

        sidecar = backup.sidecar(auth_path)
        if sidecar:
            result.backups.append(str(sidecar))
        atomicio.write_json(auth_path, desired, secret=True)
        result.files.append(str(auth_path))
        return warnings

    @staticmethod
    def _stash_login(auth: dict) -> None:
        """Keep the whole of an auth.json that holds more than an API key.

        ``tokens``, ``last_refresh`` and ``account_id`` are a ChatGPT session
        U-Pool has no way of producing again, and the clean write is about to drop
        them. Refreshed on every switch so the newest login is the one that comes
        back.
        """
        if not any(key != AUTH_ENV_KEY for key in auth):
            return
        try:
            atomicio.write_json(paths.codex_login_file(), auth, secret=True)
        except OSError:
            # A stash that cannot be written is not a reason to abandon the switch.
            return

    @staticmethod
    def _stashed_login() -> dict:
        try:
            data = atomicio.read_json(paths.codex_login_file(), {})
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def import_live(self) -> Provider | None:
        doc = self._read_config()
        slug = doc.get(SELECTED_KEY)
        if not slug:
            return None
        table = (doc.get(PROVIDERS_KEY) or {}).get(str(slug))
        if table is None:
            return None
        env_key = str(table.get("env_key") or AUTH_ENV_KEY)
        api_key = ""
        if env_key == AUTH_ENV_KEY:
            api_key = str(self._read_auth().get(AUTH_ENV_KEY) or "")
        if not api_key:
            # Where a live ``env_key = "codefast"`` setup actually keeps its key.
            # Reading it back means the imported provider is complete rather than
            # a copy of the config with the credential missing.
            api_key = winenv.read(env_key) or ""
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
            model=str(doc.get(MODEL_KEY) or ""),
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
