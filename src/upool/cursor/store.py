"""The pool of Cursor accounts, in ``~/.u-pool/cursor.json``.

Its own file rather than a section inside ``config.json`` because a section there
would not survive: :meth:`upool.store.Store._normalise` rebuilds its document
from ``_empty()`` and copies only ``apps``, so an unknown top-level key is
dropped silently on the very next save - the first provider switch after adding
an account would take the whole pool with it. A Cursor account also has no
``Provider`` to normalise through. This follows ``env-owned.json`` and
``codex-login.json``: U-Pool state that is not a provider record gets its own
file.

Plain text, written with ``secret=True``, the same protection ``config.json``
gets. Encryption was offered and declined - recorded here so it is not
re-litigated: these tokens are whole account credentials sitting in a readable
file, which is the exposure ``config.json`` already carries for provider API
keys, and a key that has to live on the same machine as the file it protects
moves the problem rather than solving it.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from .. import atomicio, paths
from ..models import UPoolError, now_ms
from .models import STATUS_UNKNOWN, CursorAccount, as_counter

if TYPE_CHECKING:  # pragma: no cover - only the annotation needs the client
    from .api import AccountFacts

CONFIG_VERSION = 1

# What a refresh is allowed to write back. ``AccountFacts.message`` is
# deliberately absent: it is the "could not refresh" line for the toast, a
# property of the attempt rather than of the account.
FACT_FIELDS = (
    "email",
    "name",
    "avatar",
    "plan",
    "plan_status",
    "usage_used",
    "usage_limit",
    "usage_unit",
    "usage_percent",
    "status",
)


def _empty() -> dict[str, Any]:
    return {"version": CONFIG_VERSION, "current": "", "accounts": []}


class CursorStore:
    """Named apart from :class:`upool.store.Store` so both can be imported at once."""

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
        path = paths.cursor_accounts_file()
        try:
            raw = atomicio.read_json(path, None)
        except ValueError as exc:
            raise UPoolError(
                f"{path} is not valid JSON. Move it aside to start fresh."
            ) from exc
        if raw is None:
            # No file is the ordinary state of an install that has never opened
            # the Cursor tab. Unlike ``Store``, nothing is bootstrapped and
            # nothing is written: an empty pool has no official entry to seed and
            # no live setup to import, so there is nothing to put on disk until
            # the user pastes a cookie.
            return _empty()
        if not isinstance(raw, dict):
            raise UPoolError(f"{path} does not contain a JSON object.")
        return self._normalise(raw)

    def _normalise(self, raw: dict[str, Any]) -> dict[str, Any]:
        data = _empty()
        data["version"] = as_counter(raw.get("version")) or CONFIG_VERSION
        accounts = [
            CursorAccount.from_dict(entry).to_dict()
            for entry in raw.get("accounts") or []
            if isinstance(entry, dict)
        ]
        known = {entry["id"] for entry in accounts}
        current = raw.get("current") or ""
        # A ``current`` naming an account that is gone would light the green dot
        # on nothing; the switch that set it is the only thing that can restore it.
        data["current"] = current if current in known else ""
        data["accounts"] = accounts
        return data

    # ---------------------------------------------------------------- saving

    def _save(self, data: dict[str, Any] | None = None) -> None:
        atomicio.write_json(
            paths.cursor_accounts_file(),
            data if data is not None else self.data(),
            secret=True,
        )

    # ---------------------------------------------------------------- reading

    def list_accounts(self) -> list[CursorAccount]:
        return [CursorAccount.from_dict(entry) for entry in self.data()["accounts"]]

    def current_id(self) -> str:
        return self.data()["current"]

    def get(self, account_id: str) -> CursorAccount:
        for account in self.list_accounts():
            if account.id == account_id:
                return account
        raise UPoolError("That account is no longer in the pool.")

    def find(self, account_id: str) -> CursorAccount | None:
        try:
            return self.get(account_id)
        except UPoolError:
            return None

    # ---------------------------------------------------------------- writing

    def upsert(self, parsed: CursorAccount) -> tuple[CursorAccount, bool]:
        """Store a pasted cookie, returning the record and whether it already existed.

        Identity is ``user_id``, not ``email`` and not ``id`` - it is the half of
        the cookie before ``::``, so it is known without a network call and it is
        the same across a rotation. That is what lets a re-paste refresh the row
        that is already there: position, name and last-known usage all stay, and
        the user gets the account back rather than a second copy of it.

        A record with no ``user_id`` or no token is not an account U-Pool can
        switch to, so it is refused here rather than stored as a row whose ``Use``
        button can never work. ``parse.py`` skips a line it cannot read, so this
        fires only on a caller that invented one.
        """
        if not parsed.user_id or not parsed.token:
            raise UPoolError("That cookie is missing its user id or its token.")
        with self._lock:
            accounts = self.data()["accounts"]
            for index, existing in enumerate(accounts):
                if existing.get("user_id") != parsed.user_id:
                    continue
                account = CursorAccount.from_dict(existing)
                rotated = account.token != parsed.token
                account.token = parsed.token
                if rotated:
                    # A 401 greys the row and disables ``Use``, and pasting a
                    # fresh cookie is how that is undone - so a new token retires
                    # the old verdict rather than inheriting it. The usage figures
                    # stay; they were true when they were measured.
                    account.status = STATUS_UNKNOWN
                # A CSV paste carries an email, ``/api/auth/me`` carries a better
                # one. Filling only a blank lets the paste complete a record the
                # network has never answered for, without overwriting anything a
                # refresh established.
                account.email = account.email or parsed.email
                account.name = account.name or parsed.name
                accounts[index] = account.to_dict()
                self._save()
                return account, True

            # Built here rather than stored as handed over, so the pool owns the
            # id and the timestamp and the caller cannot mutate a stored row.
            account = CursorAccount(
                user_id=parsed.user_id,
                token=parsed.token,
                email=parsed.email,
                name=parsed.name,
            )
            accounts.append(account.to_dict())
            self._save()
            return account, False

    def merge_facts(self, account_id: str, facts: AccountFacts) -> CursorAccount:
        """Fold what a refresh learned into the stored record, field by field.

        ``None`` means "not learned" and leaves the stored value alone. That is
        what makes partial success useful: ``/api/auth/me`` answering while
        ``/api/usage-summary`` fails gives a card with a name and the *previous*
        usage figures, rather than a name and a bar that just went blank.

        ``last_checked`` moves only when something was actually learned. A
        refresh that failed outright carries nothing but its message, and
        stamping it would put "checked just now" beside figures the check never
        touched.
        """
        with self._lock:
            accounts = self.data()["accounts"]
            for index, existing in enumerate(accounts):
                if existing.get("id") != account_id:
                    continue
                merged = dict(existing)
                learned = False
                for name in FACT_FIELDS:
                    value = getattr(facts, name, None)
                    if value is None:
                        continue
                    merged[name] = value
                    learned = True
                if learned:
                    merged["last_checked"] = now_ms()
                # Back through ``from_dict`` because every one of these values
                # came from an undocumented endpoint: an unrecognised ``status``
                # collapses to ``unknown`` here rather than reaching the UI as a
                # string it has no rendering for.
                account = CursorAccount.from_dict(merged)
                accounts[index] = account.to_dict()
                self._save()
                return account
        raise UPoolError("That account is no longer in the pool.")

    def delete(self, account_id: str) -> None:
        """Remove an account, unless it is the one Cursor is signed in as.

        The same refusal :meth:`upool.store.Store.delete` makes, for a sharper
        reason: the active row holds the only copy U-Pool has of the cookie now
        sitting in ``state.vscdb``. Dropping it would leave the editor signed in
        to an account the pool can no longer switch back to, recoverable only by
        signing in through a browser again.
        """
        with self._lock:
            data = self.data()
            remaining = [a for a in data["accounts"] if a["id"] != account_id]
            if len(remaining) == len(data["accounts"]):
                raise UPoolError("That account is no longer in the pool.")
            if data["current"] == account_id:
                raise UPoolError("This account is in use. Switch to another one first.")
            data["accounts"] = remaining
            self._save()

    def reorder(self, ordered_ids: list[str]) -> None:
        with self._lock:
            data = self.data()
            by_id = {a["id"]: a for a in data["accounts"]}
            # Sorted rather than set, so a list carrying the same id twice is
            # rejected instead of duplicating that row and losing another.
            if sorted(ordered_ids) != sorted(by_id):
                raise UPoolError("Reorder request did not match the current account list.")
            data["accounts"] = [by_id[account_id] for account_id in ordered_ids]
            self._save()

    def set_current(self, account_id: str) -> None:
        """Record which account Cursor is signed in as.

        This field describes ``state.vscdb``, not a preference, so it is only
        ever set once :mod:`upool.cursor.switch` has actually written that
        database. Setting it from anywhere else would make the green dot lie
        about which account the editor would open with.
        """
        with self._lock:
            self.get(account_id)
            self.data()["current"] = account_id
            self._save()
