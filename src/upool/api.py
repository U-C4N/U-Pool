"""The object exposed to JavaScript as ``window.pywebview.api``.

Every method returns the same envelope - ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": "..."}`` - so the UI has exactly one error path.

API keys are never included in list responses; the edit form asks for a single
provider through :meth:`get_provider` when it actually needs the value.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from . import __version__, adapters, autostart, health, paths, settings
from .models import Provider, UPoolError, as_bool, mask_secret
from .store import Store

# One version string for the package, the window title bar and the UI badge.
APP_VERSION = __version__


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

    def attach(self, window) -> None:
        self._window = window

    # ------------------------------------------------------------------ read

    @endpoint
    def bootstrap(self) -> dict[str, Any]:
        """Everything the UI needs for its first paint, in one round trip."""
        apps = adapters.all_apps()
        return {
            "version": APP_VERSION,
            "platform": sys.platform,
            "apps": apps,
            "state": {app["id"]: self._app_state(app["id"]) for app in apps},
            "settings": self._settings(),
        }

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
