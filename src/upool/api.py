"""The object exposed to JavaScript as ``window.pywebview.api``.

Every method returns the same envelope - ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": "..."}`` - so the UI has exactly one error path.

API keys are never included in list responses; the edit form asks for a single
provider through :meth:`get_provider` when it actually needs the value. The
environment reads follow the same rule - :meth:`environment` masks what it finds
in the registry, because the panel has to show that a variable is set, not what
it holds.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from . import __version__, adapters, autostart, health, paths, settings, updater, winenv
from .models import Provider, UPoolError, as_bool, mask_secret
from .store import Store

# One version string for the package, the window title bar and the UI badge.
APP_VERSION = __version__

# The Windows dialog that owns the user's own environment variables. It is a
# command with an argument rather than a document, so it goes through Popen -
# os.startfile would try to open a file called ``rundll32.exe ...``.
ENV_SETTINGS_COMMAND = ("rundll32.exe", "sysdm.cpl,EditEnvironmentVariables")


def endpoint(func: Callable) -> Callable:
    """Wrap a bridge method so no exception can ever reach the webview."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> dict[str, Any]:
        try:
            return {"ok": True, "data": func(*args, **kwargs)}
        except UPoolError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # pragma: no cover - defensive
            traceback.print_exc()
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return wrapper


def _summary(provider: Provider, current_id: str) -> dict[str, Any]:
    data = provider.to_dict()
    data.pop("api_key", None)
    data["api_key_masked"] = mask_secret(provider.api_key)
    data["has_api_key"] = bool(provider.api_key)
    data["active"] = provider.id == current_id
    return data


class Api:
    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store()
        self._window = None
        self._updater = updater.Updater()

    def attach(self, window) -> None:
        self._window = window

    # ------------------------------------------------------------------ read

    @endpoint
    def bootstrap(self) -> dict[str, Any]:
        """Everything the UI needs for its first paint, in one round trip."""
        apps = adapters.all_apps()
        payload = {
            "version": APP_VERSION,
            "platform": sys.platform,
            "apps": apps,
            "state": {app["id"]: self._app_state(app["id"]) for app in apps},
            "settings": self._settings(),
            "update": self._updater.snapshot(),
        }
        # Last, and only a thread spawn: first paint must not wait on the network,
        # and a dead one must not delay it either.
        self._updater.check_async()
        return payload

    def _app_state(self, app: str) -> dict[str, Any]:
        current = self.store.current_id(app)
        return {
            "current": current,
            "providers": [_summary(p, current) for p in self.store.list_providers(app)],
            "files": [str(p) for p in adapters.get(app).live_files()],
        }

    @endpoint
    def list_providers(self, app: str) -> dict[str, Any]:
        return self._app_state(app)

    @endpoint
    def get_provider(self, app: str, provider_id: str) -> dict[str, Any]:
        """Full record, API key included - for the edit form only."""
        return self.store.get(app, provider_id).to_dict()

    @endpoint
    def read_live_config(self, app: str) -> list[dict[str, str]]:
        files = []
        for path in adapters.get(app).live_files():
            try:
                content = path.read_text(encoding="utf-8") if path.exists() else ""
            except OSError as exc:
                content = f"<unreadable: {exc}>"
            files.append({"path": str(path), "exists": path.exists(), "content": content})
        return files

    @endpoint
    def environment(self, app: str) -> dict[str, Any]:
        """The Windows environment variables U-Pool manages for ``app``.

        Names come from both directions - the ones U-Pool already owns and the
        ones the active provider would write - so the panel says something
        before the first switch, and a name on its way out is still listed
        rather than vanishing before it has gone.
        """
        adapter = adapters.get(app)
        namespace = adapter.env_namespace
        if not namespace:
            # Claude Desktop: nothing is managed here, so there is nothing to
            # list and nothing the Windows editor would be opened for.
            return {"supported": False, "namespace": "", "vars": []}
        current = self.store.find(app, self.store.current_id(app))
        desired = adapter.env_vars(current) if current is not None else {}
        # Registry names are case-insensitive, so both lookups fold the key.
        live = {name.lower(): value for name, value in winenv.read_all().items()}
        held = {name.lower() for name in winenv.owned(namespace)}
        return {
            "supported": winenv.supported(),
            "namespace": namespace,
            "vars": [
                {
                    "name": name,
                    "value_masked": mask_secret(live.get(name.lower(), "")),
                    "owned": name.lower() in held,
                }
                for name in winenv.preview(namespace, desired)
            ],
        }

    # ----------------------------------------------------------------- write

    @endpoint
    def save_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider = Provider.from_dict(payload)
        existing = self.store.find(provider.app, provider.id) if payload.get("id") else None
        if existing is not None:
            # A blank key field in the edit form means "leave it alone".
            if not provider.api_key:
                provider.api_key = existing.api_key
            provider.official = existing.official
            saved = self.store.update(provider)
        else:
            saved = self.store.add(provider)
        return {"id": saved.id, "state": self._app_state(saved.app)}

    @endpoint
    def delete_provider(self, app: str, provider_id: str) -> dict[str, Any]:
        self.store.delete(app, provider_id)
        return self._app_state(app)

    @endpoint
    def duplicate_provider(self, app: str, provider_id: str) -> dict[str, Any]:
        copy = self.store.duplicate(app, provider_id)
        return {"id": copy.id, "state": self._app_state(app)}

    @endpoint
    def reorder_providers(self, app: str, ordered_ids: list[str]) -> dict[str, Any]:
        self.store.reorder(app, ordered_ids)
        return self._app_state(app)

    @endpoint
    def switch_provider(self, app: str, provider_id: str) -> dict[str, Any]:
        result = self.store.switch(app, provider_id)
        return {
            "state": self._app_state(app),
            "files": result.files,
            "backups": result.backups,
            "warnings": result.warnings,
            "removed": result.removed,
            "env_written": result.env_written,
            "env_removed": result.env_removed,
        }

    # ------------------------------------------------------------------ test

    @endpoint
    def test_provider(self, app: str, provider_id: str) -> dict[str, Any]:
        provider = self.store.get(app, provider_id)
        return health.check(provider).to_dict() | {"name": provider.name}

    @endpoint
    def test_all(self, app: str) -> list[dict[str, Any]]:
        providers = self.store.list_providers(app)
        names = {p.id: p.name for p in providers}
        return [r.to_dict() | {"name": names.get(r.provider_id, "")} for r in health.check_many(providers)]

    # ------------------------------------------------------------------ shell

    @endpoint
    def open_path(self, target: str) -> str:
        """Reveal a config file or folder in the OS file manager."""
        path = Path(target)
        location = path if path.is_dir() else path.parent
        location.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(location))  # noqa: S606 - intentional shell-open
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(location)])
        else:
            subprocess.Popen(["xdg-open", str(location)])
        return str(location)

    @endpoint
    def open_external(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            raise UPoolError("Only http(s) links can be opened.")
        import webbrowser

        webbrowser.open(url)
        return url

    @endpoint
    def open_env_settings(self) -> str:
        """Open the Windows editor for the user's environment variables.

        The way out of anything U-Pool got wrong in the registry, and the place
        to see the names it does not own, so it points at Windows' own dialog
        rather than growing an editor of its own.
        """
        if sys.platform != "win32":
            raise UPoolError(winenv.UNSUPPORTED_NOTE)
        subprocess.Popen(ENV_SETTINGS_COMMAND)
        return " ".join(ENV_SETTINGS_COMMAND)

    @endpoint
    def app_paths(self) -> dict[str, str]:
        return {
            "home": str(paths.app_home()),
            "config": str(paths.config_file()),
            "backups": str(paths.backup_dir()),
            "settings": str(paths.settings_file()),
        }

    # --------------------------------------------------------------- preferences

    def _settings(self) -> dict[str, Any]:
        """App preferences, with the OS as the authority on the startup entry."""
        stored = settings.load()
        entry = autostart.state()
        live = entry["enabled"] if entry["supported"] else stored["launch_at_startup"]
        return {
            "launch_at_startup": bool(live),
            "autostart_supported": bool(entry["supported"]),
            "autostart_blocked": bool(entry["blocked"]),
            "autostart_command": str(entry["command"]),
            "autostart_detail": str(entry["detail"]),
            "backup_enabled": bool(stored["backup_enabled"]),
            "update_check_enabled": bool(stored["update_check_enabled"]),
        }

    @endpoint
    def get_settings(self) -> dict[str, Any]:
        return self._settings()

    @endpoint
    def set_launch_at_startup(self, enabled: bool) -> dict[str, Any]:
        """Add or remove the OS sign-in entry, and remember the choice."""
        wanted = as_bool(enabled)
        autostart.apply(wanted)
        try:
            settings.update({"launch_at_startup": wanted})
        except OSError as exc:
            # The OS entry already changed, so say which half failed rather than
            # letting a bare errno suggest nothing happened.
            raise UPoolError(
                f"The startup entry was {'added' if wanted else 'removed'}, but the choice "
                f"could not be saved to {paths.settings_file()}: {exc}"
            ) from exc
        return self._settings()

    @endpoint
    def set_backup_enabled(self, enabled: bool) -> dict[str, Any]:
        """Turn the copy kept beside every file U-Pool writes on or off.

        Answers with the whole settings block rather than an acknowledgement, so
        the panel renders what was stored instead of what was asked for.
        """
        try:
            settings.update({"backup_enabled": as_bool(enabled)})
        except OSError as exc:
            raise UPoolError(f"That choice could not be saved: {exc}") from exc
        return self._settings()

    # ------------------------------------------------------------------ updates

    @endpoint
    def update_status(self) -> dict[str, Any]:
        """Current snapshot. Polled by the UI while an update is in flight."""
        return self._updater.snapshot()

    @endpoint
    def check_updates(self, force: bool = False) -> dict[str, Any]:
        return self._updater.check_async(force=as_bool(force))

    @endpoint
    def install_update(self) -> dict[str, Any]:
        """Download and stage the newer release, then hand over to the swapper."""
        return self._updater.install_async()

    @endpoint
    def skip_update(self, version: str) -> dict[str, Any]:
        return self._updater.skip(str(version))

    @endpoint
    def set_update_checks(self, enabled: bool) -> dict[str, Any]:
        return self._updater.set_checks_enabled(as_bool(enabled))

    @endpoint
    def open_startup_settings(self) -> str:
        """Open the Windows page that owns the final say on startup apps."""
        if sys.platform != "win32":
            raise UPoolError(autostart.UNSUPPORTED_NOTE)
        os.startfile(autostart.SETTINGS_URI)  # noqa: S606 - intentional shell-open
        return autostart.SETTINGS_URI

    @endpoint
    def quit(self) -> bool:
        if self._window is not None:
            self._window.destroy()
        return True
