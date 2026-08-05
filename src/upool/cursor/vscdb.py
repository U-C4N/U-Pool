"""Cursor's ``state.vscdb``, written one named key at a time.

This is the most dangerous write target U-Pool has ever had. The file is 1.38 MB
on the machine this was written for and holds three tables: ``ItemTable``,
``cursorDiskKV`` and ``composerHeaders``. The last two are the user's
conversations and their composer state. ``ItemTable`` is not innocent either - it
carries the live ``mcpOAuth.secret.*`` rows, which are working OAuth credentials
for their MCP servers, in among the editor's own settings.

So the claim is as narrow as it can be made: **the ``ItemTable`` rows whose key
is in :data:`AUTH_KEYS`, and nothing else.** Every statement in this module is a
``SELECT`` or an ``INSERT OR REPLACE`` naming those keys. There is no ``DELETE``,
no ``DROP``, no ``VACUUM``, no rewrite of the file. ``cursorDiskKV`` and
``composerHeaders`` are never opened and no other ``ItemTable`` row is so much as
read. Ownership is by exact key, never by the ``cursorAuth/`` prefix - a prefix
sweep would take whatever Cursor adds under it next. Widening any of that is a
data-loss change.

``storage.json`` and ``machineId`` are not touched here or anywhere else in
U-Pool. The reference implementation resets those telemetry ids; that is not part
of changing accounts, it is defeating a per-device limit, and they are Cursor's
own state rather than anything U-Pool wrote.

The schema, read off the live database rather than assumed::

    CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)

The column is declared ``BLOB`` and all 100 rows on that machine store TEXT, so a
write binds ``str`` and lands as ``text`` - the flavour every neighbouring row
already has. Reading is the tolerant direction and accepts either, because a
future Cursor storing a real blob should render an odd card, not raise.

Cursor is expected to be closed before anything here runs, and closing it is
:mod:`upool.cursor.switch`'s job. A locked database therefore means the close did
not take, and this module says so instead of writing around it: SQLite offers
several ways past a busy writer and every one of them is a way to damage a file
holding somebody's entire chat history.

The backup belongs to :mod:`upool.cursor.switch` as well - ``backup.sidecar()``
runs there, once, before the write. Nothing here takes a second copy.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import paths
from ..models import UPoolError

# Measured, not assumed. Cursor 3.14.7 was snapshotted signed out (100 ItemTable
# rows), signed in, and snapshotted again (148): 50 rows appeared and 18 changed.
# These are the ones that are the account rather than the session's window layout,
# its theme or the 594 KB of server-pushed experiment config that arrived with them.
#
# Two of these are not in any existing switcher, because they are new in the 3.x
# "glass" builds: ``cachedScopedProfile`` (the display name and avatar the account
# menu draws) and ``stripeSubscriptionStatus`` (which is separate from
# ``stripeMembershipType`` - the pair reads "free" / "unpaid" on this machine).
#
# ``glass.lastSignedInAuthId`` is a WIDENING of the claim as the 0.8.0 design wrote
# it: that document says ``cursorAuth/*`` and nothing else, and this key sits
# outside the prefix. It is here because the sign-in wrote it and it holds the same
# identity the token does - ``auth0|user_01...``, matching the JWT's ``sub``. Left
# alone, a switch would leave Cursor holding account B's token beside account A's
# id, and a disagreement between those two is not a state any measurement showed
# Cursor in. It is one key, named in full, and it is the only one outside the prefix.
#
# ``cursorAuth/onboardingDate`` is deliberately NOT owned, though the sign-in wrote
# it too. U-Pool cannot produce it - a pasted cookie does not carry one - so owning
# it would mean blanking it on every switch, and a blank onboarding date is how you
# ask Cursor to run onboarding again. Leaving the previous account's date costs
# nothing; the narrower claim wins.
AUTH_KEYS: tuple[str, ...] = (
    "cursorAuth/accessToken",
    "cursorAuth/refreshToken",
    "cursorAuth/cachedEmail",
    "cursorAuth/cachedSignUpType",
    "cursorAuth/cachedScopedProfile",
    "cursorAuth/stripeMembershipType",
    "cursorAuth/stripeSubscriptionStatus",
    "glass.lastSignedInAuthId",
)

# The caller guarantees Cursor is closed, so this is not a lock to wait out - it
# is long enough to ride over the last flush of a process that just exited, and
# short enough that a Cursor which did not close reports quickly.
TIMEOUT = 5.0


def exists() -> bool:
    """Whether there is a Cursor database here at all.

    False on a machine where Cursor has never been installed or never launched,
    which is a Cursor tab that explains itself rather than a switch that fails.
    """
    return paths.cursor_state_db().is_file()


def read_auth(db: Path | None = None) -> dict[str, str]:
    """The owned keys as they currently stand, absent ones simply missing.

    Only :data:`AUTH_KEYS` is selected. This is not an optimisation - reading the
    rest of ``ItemTable`` would pull the user's MCP OAuth secrets into a process
    that has no business holding them.
    """
    path = _target(db)
    if not AUTH_KEYS or not path.is_file():
        return {}

    connection = _connect(path)
    try:
        rows = connection.execute(_select_sql(), AUTH_KEYS).fetchall()
    except sqlite3.Error as exc:
        raise _failure(exc) from exc
    finally:
        connection.close()
    return {key: _as_text(value) for key, value in rows}


def write_auth(values: dict[str, str], db: Path | None = None) -> list[str]:
    """Store ``values`` for the owned keys, returning the keys actually written.

    ``db`` exists so :mod:`upool.cursor.switch` can pass the very path it backed
    up a moment earlier, rather than resolving the location twice and hoping the
    two agree.

    A key outside the claim is refused, not quietly dropped. Dropping it would
    hand back a Cursor that will not sign in with nothing to point at, and the
    same rule is what makes an unmeasured :data:`AUTH_KEYS` fail loudly instead of
    reporting a successful switch that wrote nothing.

    All of the keys land or none do. A half-applied auth record is an editor that
    can neither sign in nor sign out.
    """
    path = _target(db)
    _reject_unowned(values)
    ordered = [key for key in AUTH_KEYS if key in values]
    if not ordered:
        return []
    if not path.is_file():
        # Connecting would create the file, and an empty database where Cursor
        # expects its own is not something U-Pool should be able to invent.
        raise UPoolError(f"Cursor has no database at {path} - is Cursor installed?")

    _run(path, [(key, values[key]) for key in ordered])
    return ordered


def clear_auth(db: Path | None = None) -> list[str]:
    """Blank the owned keys that are present, returning the ones blanked.

    The rows stay. Cursor wrote them and a signed-out Cursor has them empty, so
    ``DELETE`` would be U-Pool removing rows to reach a state an empty string
    already describes - and ``DELETE FROM ItemTable`` is one forgotten ``WHERE``
    away from the user's settings.

    An owned key that is not there is left alone rather than created empty, for
    the mirror of the same reason: absent and blank mean the same thing to Cursor,
    and only one of them adds a row it never had.
    """
    path = _target(db)
    if not AUTH_KEYS or not path.is_file():
        return []

    connection = _connect(path)
    try:
        rows = connection.execute(_select_sql(), AUTH_KEYS).fetchall()
    except sqlite3.Error as exc:
        raise _failure(exc) from exc
    finally:
        connection.close()

    found = {key for key, _ in rows}
    present = [key for key in AUTH_KEYS if key in found]
    if present:
        _run(path, [(key, "") for key in present])
    return present


def _target(db: Path | None) -> Path:
    path = paths.cursor_state_db() if db is None else Path(db)
    _guard(path)
    return path


def _guard(path: Path) -> None:
    """Refuse any target that is not under the directory Cursor keeps its state in.

    Anchored on ``paths.cursor_app_dir()`` rather than on the home directory the
    way ``sessions._guard`` is, because on Windows that directory resolves through
    ``%APPDATA%`` and need not sit under the home at all.

    Nothing a user types reaches here - the path comes from a helper or from
    ``switch.py`` - so this cannot fire on input. It fires when path resolution
    goes wrong, which is exactly the failure that would point an
    ``INSERT OR REPLACE`` at a file nobody chose.
    """
    root = paths.cursor_app_dir()
    try:
        resolved = path.resolve()
        anchor = root.resolve()
    except OSError as exc:
        raise UPoolError(f"{path} could not be resolved.") from exc
    if resolved == anchor or anchor not in resolved.parents:
        raise UPoolError(f"{path} is outside Cursor's own directory and was not touched.")


def _connect(path: Path) -> sqlite3.Connection:
    try:
        return sqlite3.connect(path, timeout=TIMEOUT)
    except sqlite3.Error as exc:
        raise _failure(exc) from exc


def _run(path: Path, pairs: list[tuple[str, str]]) -> None:
    """Apply ``pairs`` in one transaction, or leave the file exactly as it was."""
    connection = _connect(path)
    try:
        with connection:
            connection.executemany(
                "INSERT OR REPLACE INTO ItemTable(key, value) VALUES (?, ?)", pairs
            )
    except sqlite3.Error as exc:
        raise _failure(exc) from exc
    finally:
        connection.close()


def _select_sql() -> str:
    """``SELECT`` over the owned keys, one placeholder per key.

    The only thing interpolated is a run of question marks counted off
    :data:`AUTH_KEYS`; the keys themselves are always bound.
    """
    placeholders = ", ".join("?" * len(AUTH_KEYS))
    return f"SELECT key, value FROM ItemTable WHERE key IN ({placeholders})"


def _reject_unowned(values: dict[str, str]) -> None:
    unowned = [key for key in values if key not in AUTH_KEYS]
    if unowned and not AUTH_KEYS:
        raise UPoolError(
            "U-Pool does not yet know which keys Cursor writes when it signs in, "
            "so it wrote nothing. That list comes from the sign-in diff in the "
            "0.8.0 design, section 6."
        )
    if unowned:
        names = ", ".join(sorted(unowned))
        raise UPoolError(f"U-Pool does not own these keys in Cursor's database: {names}.")
    for key in values:
        if not isinstance(values[key], str):
            # Every neighbouring row is TEXT; binding anything else would store a
            # different SQLite type under a key Cursor expects to parse as a string.
            raise UPoolError(f"The value for '{key}' is not text.")


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _failure(exc: sqlite3.Error) -> UPoolError:
    detail = str(exc)
    if "locked" in detail.lower() or "busy" in detail.lower():
        return UPoolError(
            "Cursor still has its database open, so nothing was written. "
            "Close Cursor and try again."
        )
    return UPoolError(f"Cursor's database could not be used: {detail}")
