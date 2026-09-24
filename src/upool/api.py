"""The object exposed to JavaScript as ``window.pywebview.api``.

Every method returns the same envelope - ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": "..."}`` - so the UI has exactly one error path.

API keys are never included in list responses; the edit form asks for a single
provider through :meth:`get_provider` when it actually needs the value. The
environment reads follow the same rule - :meth:`environment` masks what it finds
in the registry, because the panel has to show that a variable is set, not what
it holds.

The Cursor endpoints hold the same rule harder still, and with no exception: a
session cookie is the whole account rather than one endpoint's access to it, so
there is no ``get_account`` counterpart to :meth:`get_provider` and no form that
asks for the value back. The UI gets ``has_token`` and nothing else.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from . import (
    __version__,
    adapters,
    atomicio,
    autostart,
    clis,
    health,
    paths,
    sessions,
    settings,
    updater,
    winenv,
)
from .cursor import api as cursorapi
from .cursor import parse as cursorparse
from .cursor import process as cursorproc
from .cursor import switch as cursorswitch
from .cursor import vscdb as cursorvscdb
from .cursor.models import CursorAccount
from .cursor.store import CursorStore
from .models import Provider, UPoolError, as_bool, mask_secret
from .store import Store
from .usage import pricing
from .usage import service as usage_service

# One version string for the package, the window title bar and the UI badge.
APP_VERSION = __version__

# The Windows dialog that owns the user's own environment variables. It is a
# command with an argument rather than a document, so it goes through Popen -
# os.startfile would try to open a file called ``rundll32.exe ...``.
ENV_SETTINGS_COMMAND = ("rundll32.exe", "sysdm.cpl,EditEnvironmentVariables")

# What the account already signed in to Cursor is called when it is adopted into
# the pool. It is a placeholder rather than a guess: nothing knows whose account
# it is until ``/api/auth/me`` answers, and the first refresh replaces it.
LIVE_SESSION_NAME = "Current session"


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
        # One pool for the life of the app. The switch is handed this instance
        # rather than building its own, so the ``current`` it records is the one
        # the next ``cursor_state`` reads.
        self._cursor = CursorStore()
        self._cursor_lock = threading.Lock()
        self._cursor_worker: threading.Thread | None = None
        self._adopt_live_session()

    def attach(self, window) -> None:
        self._window = window

    # ------------------------------------------------------------------ read

    @endpoint
    def bootstrap(self) -> dict[str, Any]:
        """Everything the UI needs for its first paint, in one round trip."""
        apps = adapters.all_apps()
        # Started before the snapshot is taken, not after: a snapshot read first
        # reports busy=false on a probe that is about to begin, and the UI only
        # polls while busy is true - so the header would sit on placeholders until
        # someone pressed refresh. Spawning a thread costs nothing here.
        cli_state = clis.refresh_async(force=False)
        # The Cursor pool for the same reason one line further on: every card is
        # a plan, a usage bar and an email that only cursor.com knows, and a
        # snapshot taken before the refresh starts reports busy=false on a
        # refresh that is about to begin - so the cards would sit on their last
        # known figures until someone pressed Refresh all.
        cursor_state = self._start_cursor_refresh()
        payload = {
            "version": APP_VERSION,
            "platform": sys.platform,
            "apps": apps,
            "state": {app["id"]: self._app_state(app["id"]) for app in apps},
            "settings": self._settings(),
            "update": self._updater.snapshot(),
            "clis": cli_state,
            "cursor": cursor_state,
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

    # ------------------------------------------------------------ CLI versions

    @endpoint
    def cli_versions(self) -> dict[str, Any]:
        """What is installed, from cache. Polled while ``busy`` is true."""
        return clis.snapshot()

    @endpoint
    def refresh_cli_versions(self) -> dict[str, Any]:
        return clis.refresh_async(force=True)

    # --------------------------------------------------------------- sessions

    @endpoint
    def session_summary(self, app: str) -> dict[str, Any]:
        """How much there is to delete, so the button can say it first."""
        return sessions.summary(app)

    @endpoint
    def delete_sessions(self, app: str) -> dict[str, Any]:
        """Erase this app's transcripts. Irreversible, and nothing is copied first.

        Answers with the fresh summary as well as the outcome, so a partial delete -
        a file the running CLI still holds open - shows as what is left rather than
        as a success followed by a stale count.
        """
        # Bank the usage totals before the purge deletes the transcripts they are
        # computed from - the one coupling between the two subsystems (spec §4).
        usage_service.refresh(app)
        outcome = sessions.purge(app)
        return outcome | {"summary": sessions.summary(app)}

    # ------------------------------------------------------------------ usage

    @endpoint
    def usage_summary(self, range_key: str = "30d") -> dict[str, Any]:
        return usage_service.summary(range_key)

    @endpoint
    def usage_refresh(self, range_key: str = "30d") -> dict[str, Any]:
        usage_service.refresh()
        return usage_service.summary(range_key)

    @endpoint
    def get_pricing(self) -> dict[str, Any]:
        p = pricing.Pricing.load()
        return {"builtin": pricing.BUILTIN, "merged": p.merged(), "overrides": p.overrides()}

    @endpoint
    def set_pricing(self, overrides: dict[str, Any]) -> dict[str, Any]:
        atomicio.write_json(paths.pricing_file(), overrides)
        p = pricing.Pricing.load()
        return {"builtin": pricing.BUILTIN, "merged": p.merged(), "overrides": p.overrides()}

    # ----------------------------------------------------------------- cursor
    #
    # The sixth tab, and the only one that is not a provider app. Every endpoint
    # here answers with the whole :meth:`_cursor_state` for the same reason the
    # provider endpoints answer with ``_app_state``: the UI renders what was
    # stored rather than what it asked for, so a paste that was deduplicated or
    # a switch that only half applied shows as what actually happened.

    def _cursor_state(self) -> dict[str, Any]:
        """The whole Cursor tab in one object - the sibling of :meth:`_app_state`.

        ``running`` is a live probe rather than a remembered flag. The user can
        close Cursor between opening the tab and pressing Use, and the modal that
        warns them their editor is about to be closed has to be right at the
        moment it is shown, not at the moment the tab was opened.

        ``supported`` is whether there is a Cursor database on this machine at
        all, which is the fact a switch actually depends on - a platform check
        would say yes on a Windows box where Cursor has never been installed.
        Accounts can still be pasted and refreshed without one; only ``Use``
        needs it.
        """
        current = self._cursor.current_id()
        return {
            "accounts": [
                account.summary(account.id == current) for account in self._cursor.list_accounts()
            ],
            "current": current,
            "busy": self._cursor_busy(),
            "running": cursorproc.running(),
            "supported": cursorvscdb.exists(),
            "db_path": str(paths.cursor_state_db()),
        }

    @endpoint
    def cursor_state(self) -> dict[str, Any]:
        """Current knowledge, without waiting for anything. Polled while ``busy``."""
        return self._cursor_state()

    @endpoint
    def cursor_add(self, text: str) -> dict[str, Any]:
        """Read every account out of one paste - a line, a file, or two hundred rows.

        Nothing here reaches the network. The counts are what the toast says,
        and the UI follows with :meth:`cursor_refresh` to fill the new cards in -
        which keeps every request to cursor.com behind the one endpoint that
        announces itself as making them, rather than hiding a second one inside
        a call the user thinks of as saving a cookie.

        A line that parses into an account already in the pool refreshes that row
        rather than adding a second one - identity is ``user_id``, which survives
        a rotated cookie.
        """
        result = cursorparse.parse(str(text or ""))
        added = refreshed = 0
        for parsed in result.accounts:
            # ``ParsedAccount`` carries no name because no input form has one, so
            # a new row stays nameless until /api/auth/me answers for it.
            _, existed = self._cursor.upsert(
                CursorAccount(user_id=parsed.user_id, token=parsed.token, email=parsed.email)
            )
            if existed:
                refreshed += 1
            else:
                added += 1
        return {
            "added": added,
            "refreshed": refreshed,
            "skipped": result.skipped,
            "state": self._cursor_state(),
        }

    @endpoint
    def cursor_delete(self, account_id: str) -> dict[str, Any]:
        self._cursor.delete(str(account_id))
        return self._cursor_state()

    @endpoint
    def cursor_reorder(self, ordered_ids: list[str]) -> dict[str, Any]:
        self._cursor.reorder([str(account_id) for account_id in ordered_ids])
        return self._cursor_state()

    @endpoint
    def cursor_refresh(self, account_id: str = "") -> dict[str, Any]:
        """Ask cursor.com about one account, or about every account when blank.

        Returns at once with ``busy`` true and the cards as they stand; the UI
        polls :meth:`cursor_state` until it goes false. The same shape as
        ``clis.refresh_async`` and the update check, for a sharper reason than
        either: this is every account in the pool times four undocumented
        endpoints, and no button press may block on that.
        """
        return self._start_cursor_refresh(str(account_id))

    @endpoint
    def cursor_use(self, account_id: str) -> dict[str, Any]:
        """Sign Cursor in as this account, closing and restarting the editor if it is up.

        ``self._cursor`` is handed over rather than letting the switch build its
        own store: this instance's document is what the next
        :meth:`cursor_state` reads, and a second one would record the new
        ``current`` on disk while the green dot stayed where it was.

        ``files``, ``backups`` and ``warnings`` are spread beside the state
        exactly as :meth:`switch_provider` spreads them, so the toast after a
        Cursor switch is the same toast as after a Claude switch.
        """
        outcome = cursorswitch.use(str(account_id), self._cursor)
        return {
            "state": self._cursor_state(),
            "files": outcome.result.files,
            "backups": outcome.result.backups,
            "warnings": outcome.result.warnings,
            "closed_cursor": outcome.closed_cursor,
            "relaunched": outcome.relaunched,
        }

    # ------------------------------------------------------- cursor background

    def _cursor_busy(self) -> bool:
        worker = self._cursor_worker
        return worker is not None and worker.is_alive()

    def join_cursor_refresh(self, timeout: float = 5.0) -> None:
        """Wait for an in-flight refresh. For tests; nothing in the app needs it.

        The hazard ``clis.join_worker`` exists for, one step worse: a worker
        still running when a test ends would merge cursor.com's answers into
        whatever pool the next test's sandbox points at.
        """
        worker = self._cursor_worker
        if worker is not None:
            worker.join(timeout=timeout)

    def _start_cursor_refresh(self, account_id: str = "") -> dict[str, Any]:
        """Start a background refresh unless one is already in flight.

        An account with no token stored is left out. ``cookie_header`` refuses
        one, so including it would spend a worker to record a message the card
        already makes plain.
        """
        with self._cursor_lock:
            if not self._cursor_busy():
                accounts = [a for a in self._cursor_targets(account_id) if a.token]
                if accounts:
                    self._cursor_worker = threading.Thread(
                        target=self._run_cursor_refresh,
                        args=(accounts,),
                        name="upool-cursor",
                        daemon=True,
                    )
                    self._cursor_worker.start()
        return self._cursor_state()

    def _cursor_targets(self, account_id: str) -> list[CursorAccount]:
        """One named account, or all of them when the id is blank.

        An unknown id raises rather than refreshing nothing: a Refresh pressed on
        a card that is no longer there is worth a sentence, and the endpoint
        wrapper turns it into one.
        """
        if account_id:
            return [self._cursor.get(account_id)]
        return self._cursor.list_accounts()

    def _run_cursor_refresh(self, accounts: list[CursorAccount]) -> None:
        """Fetch on a background thread and fold each answer into the pool.

        Unlike ``clis``, there is no cache to leave in a sane state on the way
        out: the store is the cache and ``busy`` is the thread being alive, so
        it clears itself however this ends. That is why a failure is printed and
        dropped rather than written back over the accounts as a row of errors -
        yesterday's plan and usage are better than blanks, which is the same
        rule ``AccountFacts`` applies one call down.
        """
        try:
            for facts in cursorapi.fetch_many(accounts):
                try:
                    self._cursor.merge_facts(facts.account_id, facts)
                except UPoolError:
                    # Deleted while its answer was in flight.
                    continue
        except Exception:  # noqa: BLE001 - a refresh must never take the app down
            traceback.print_exc()

    def _adopt_live_session(self) -> None:
        """Put the account Cursor is signed in as into the pool, if it is not there.

        Without it a switch throws away a session the user has no other copy of:
        the pool holds only what they pasted, the editor's own account is
        overwritten, and there is nothing to switch back to.
        ``Store._first_entries`` imports a live provider setup for exactly this
        reason.

        The test is **whether that user id is pooled**, not whether the pool is
        empty. Empty was the obvious rule and it defeats the purpose: paste one
        cookie before U-Pool has ever seen the editor - which is the first thing
        anybody does - and the live session is never adopted, so the first Use
        destroys it. Add-only and keyed on ``user_id``, so it cannot duplicate a
        row and cannot overwrite a token that was pasted for the same account.

        The cost is that deleting the signed-in account brings it back next
        launch. That is the right way round: a row you delete twice is a
        nuisance, and a credential with no other copy is gone.

        The assembly is :func:`upool.cursor.switch.live_account`, next to the
        function that writes those same keys. An earlier version ran the stored
        values through the paste parser, on the theory that one of them would be
        a cookie; the sign-in measurement settled that none of them is. Cursor
        keeps the token bare and the identity in a separate row, so the parser
        found nothing on every start - which is what an unmeasured guess looks
        like when it is wrong.
        """
        try:
            live = cursorswitch.live_account()
            if live is None:
                return
            pooled = self._cursor.list_accounts()
            if any(existing.user_id == live.user_id for existing in pooled):
                return
            live.name = live.name or LIVE_SESSION_NAME
            account, _ = self._cursor.upsert(live)
            # Cursor wrote that database itself, so this records what is in it
            # rather than claiming a switch happened - which is what set_current
            # is for. The only caller outside switch.py entitled to say it.
            self._cursor.set_current(account.id)
        except (UPoolError, OSError):
            # A Cursor database that cannot be read, or a pool that cannot be
            # written, must not stop the app from starting. Same refusal
            # ``Store._first_entries`` makes about a broken live config.
            return

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
            "pricing": str(paths.pricing_file()),
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
