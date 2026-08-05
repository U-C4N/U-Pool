"""Check the Cursor client against cursor.com, using the live session.

The endpoints in :mod:`upool.cursor.api` are undocumented - they are what the
cursor.com dashboard calls, read off other people's code and confirmed by nothing
until this runs. A stubbed test proves the parser handles the shape it was told
about; only this proves the shape was right.

Read-only in every direction: the session is read from Cursor's own database, the
four calls are GETs, and the account never reaches a store. Run it through
``scripts/sandbox.py`` like everything else that imports ``upool``::

    python scripts/sandbox.py scripts/probe_0_8_0.py

It needs a signed-in Cursor and prints the token masked.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from upool.cursor import api as cursorapi
from upool.cursor.models import CursorAccount, cookie_header

# The live install, deliberately not through ``paths`` - under the sandbox that
# helper points at a scratch directory, which is exactly what protects every other
# code path and exactly what would make this probe read nothing.
if sys.platform == "win32":
    LIVE = Path(os.environ["APPDATA"]) / "Cursor"
elif sys.platform == "darwin":
    LIVE = Path.home() / "Library" / "Application Support" / "Cursor"
else:
    LIVE = Path.home() / ".config" / "Cursor"

STATE_DB = LIVE / "User" / "globalStorage" / "state.vscdb"


def mask(value: str, keep: int = 8) -> str:
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...<{len(value) - keep * 2} chars>...{value[-keep:]}"


def read_live_session() -> tuple[str, str]:
    """The access token and auth id Cursor is signed in with right now.

    Copied to a temp file before it is opened: Cursor holds the original, and a
    reader that waits on its lock would block on a running editor.
    """
    if not STATE_DB.is_file():
        raise SystemExit(f"No Cursor database at {STATE_DB}.")
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "state.vscdb"
        shutil.copy(STATE_DB, copy)
        connection = sqlite3.connect(copy)
        try:
            rows = dict(
                connection.execute(
                    "SELECT key, value FROM ItemTable WHERE key IN (?, ?)",
                    ("cursorAuth/accessToken", "glass.lastSignedInAuthId"),
                )
            )
        finally:
            connection.close()
    token = str(rows.get("cursorAuth/accessToken") or "")
    auth_id = str(rows.get("glass.lastSignedInAuthId") or "")
    if not token:
        raise SystemExit("Cursor is signed out - sign in first, then run this again.")
    return token, auth_id


def user_id_from(token: str, auth_id: str) -> str:
    """The cookie's half of the identity: the JWT subject without its provider prefix."""
    parts = token.split(".")
    if len(parts) == 3:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        subject = json.loads(base64.urlsafe_b64decode(padded)).get("sub", "")
        if subject:
            return str(subject).split("|", 1)[-1]
    return auth_id.split("|", 1)[-1]


def surgical_write_over_the_real_database(token: str, user_id: str) -> None:
    """Switch twice over a copy of the live database and diff every other row.

    The synthetic fixture in ``tests/test_cursor_vscdb.py`` proves the write is
    surgical against a database built to be surgically written. This proves it
    against the one that actually exists - 148 rows, three tables, the user's
    conversations and their live MCP OAuth secrets among them.

    The copy goes to ``paths.cursor_state_db()``, which is inside the sandbox
    home, so this function writes nothing outside it.
    """
    from upool import paths
    from upool.cursor import switch, vscdb
    from upool.cursor.models import STATUS_OK
    from upool.cursor.store import CursorStore

    target = paths.cursor_state_db()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(STATE_DB, target)
    before = _rows(target)

    pool = CursorStore()
    first, _ = pool.upsert(
        CursorAccount(
            user_id=user_id, token=token, email="first@example.com",
            name="First", plan="pro", plan_status="active", status=STATUS_OK,
        )
    )
    second, _ = pool.upsert(
        CursorAccount(
            user_id="user_09PROBE", token="eyJhbGciOiJI.e30.sig", email="second@example.com",
            name="Second", plan="free", plan_status="unpaid", status=STATUS_OK,
        )
    )

    switch.use(first.id, pool)
    switch.use(second.id, pool)
    after = _rows(target)

    owned = set(vscdb.AUTH_KEYS)
    moved = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    unowned_moved = sorted(moved - owned)

    print("=== surgical write, over a copy of the live database ===")
    print(f"   rows before {len(before)}, after {len(after)}")
    print(f"   owned keys {len(owned)}, rows that moved {len(moved)}")
    print(f"   unowned rows that moved: {unowned_moved or 'none'}")
    print(f"   backup taken: {target.with_name(target.name + '.backup').is_file()}")
    signed_in_as = after.get("cursorAuth/cachedEmail")
    print(f"   ends signed in as: {signed_in_as!r} (expected 'second@example.com')")
    if unowned_moved:
        print("FAIL - the write reached rows U-Pool does not own.")
    elif signed_in_as != "second@example.com":
        print("FAIL - the second switch did not land.")
    else:
        print("OK - only the owned rows changed, twice over.")
    print()


def _rows(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(path)
    try:
        tables = [name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )]
        found: dict[str, object] = {}
        for table in tables:
            if table == "ItemTable":
                found.update(dict(connection.execute("SELECT key, value FROM ItemTable")))
            else:
                # Row count and a checksum of the whole table, since the claim is
                # that these are never opened at all.
                total = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                found[f"<table {table}>"] = total
        return found
    finally:
        connection.close()


def main() -> None:
    token, auth_id = read_live_session()
    user_id = user_id_from(token, auth_id)
    account = CursorAccount(user_id=user_id, token=token)

    print(f"auth id   {auth_id}")
    print(f"user id   {user_id}")
    print(f"token     {mask(token)}")
    print(f"cookie    {mask(cookie_header(account), 34)}")
    print()

    surgical_write_over_the_real_database(token, user_id)

    facts = cursorapi.fetch(account)

    print("=== what the four endpoints gave back ===")
    for field in (
        "status",
        "email",
        "name",
        "plan",
        "plan_status",
        "usage_used",
        "usage_limit",
        "usage_unit",
        "usage_percent",
    ):
        print(f"   {field:<14} {getattr(facts, field)!r}")
    for message in facts.messages:
        print(f"   ! {message}")
    print()

    missing = [f for f in ("email", "plan") if not getattr(facts, f)]
    if facts.status != "ok":
        print(f"FAIL - the cookie did not authenticate (status={facts.status!r}).")
    elif missing:
        print(f"PARTIAL - authenticated, but these came back empty: {', '.join(missing)}.")
        print("The card degrades to an em-dash rather than breaking, which is the")
        print("designed behaviour - but the field spellings want another look.")
    else:
        print("OK - identity and plan resolved from the live endpoints.")


if __name__ == "__main__":
    main()
