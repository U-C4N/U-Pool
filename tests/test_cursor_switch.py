from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

import pytest

from upool import paths
from upool.cursor import process, switch, vscdb
from upool.cursor.models import STATUS_EXPIRED, CursorAccount
from upool.cursor.store import CursorStore
from upool.models import UPoolError

# Stands in for the sign-in diff in section 6 of the 0.8.0 design, the same way
# tests/test_cursor_vscdb.py's FAKE_KEYS does. switch.py is written over
# ``_auth_values`` and ``vscdb.AUTH_KEYS`` and names no key of its own, so a
# synthetic pair exercises the real flow today and keeps passing unchanged once
# the measured list lands.
FAKE_KEYS = ("upoolTest/accessToken", "upoolTest/cachedEmail")

# A neighbour under the prefix the real keys will live under, present in every
# database these tests build. Nothing here owns it, so nothing here may move it.
NEIGHBOUR = "cursorAuth/stripeMembershipType"


def fake_auth_values(account: CursorAccount) -> dict[str, str]:
    return {
        "upoolTest/accessToken": account.token,
        "upoolTest/cachedEmail": account.email,
    }


@pytest.fixture
def measured(monkeypatch):
    """Fill in the pending measurement, so the tests below are about the switch."""
    monkeypatch.setattr(vscdb, "AUTH_KEYS", FAKE_KEYS)
    monkeypatch.setattr(switch, "_auth_values", fake_auth_values)


@pytest.fixture
def db() -> Path:
    """A ``state.vscdb`` with the real ``ItemTable`` declaration and one unowned row."""
    path = paths.cursor_state_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)"
        )
        connection.execute("INSERT INTO ItemTable VALUES (?, ?)", (NEIGHBOUR, "free"))
    connection.close()
    return path


@pytest.fixture
def pool() -> CursorStore:
    store = CursorStore()
    store.upsert(CursorAccount(user_id="user_01AB", token="eyJ-first", email="a@example.com"))
    store.upsert(CursorAccount(user_id="user_02CD", token="eyJ-second", email="b@example.com"))
    return store


def first(pool: CursorStore) -> CursorAccount:
    return pool.list_accounts()[0]


def rows(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(path)
    try:
        return dict(connection.execute("SELECT key, value FROM ItemTable").fetchall())
    finally:
        connection.close()


def record(monkeypatch, *, running=False, closes=True, launches=True) -> list[str]:
    """Log every call the switch makes to the editor and to the database.

    ``vscdb.write_auth`` is stubbed rather than run so that the order can be
    asserted without a real write; the two tests that care whether anything
    actually landed use the real one.
    """
    calls: list[str] = []

    def _running() -> bool:
        calls.append("running")
        return running

    def _close(timeout: float = 0.0) -> bool:
        calls.append(f"close({timeout})")
        return closes

    def _launch() -> bool:
        calls.append("launch")
        return launches

    def _write_auth(values, db=None):
        calls.append("write_auth")
        return list(values)

    monkeypatch.setattr(process, "running", _running)
    monkeypatch.setattr(process, "close", _close)
    monkeypatch.setattr(process, "launch", _launch)
    monkeypatch.setattr(vscdb, "write_auth", _write_auth)
    return calls


def test_a_closed_cursor_is_never_asked_to_close(measured, db, pool, monkeypatch):
    calls = record(monkeypatch, running=False)
    account = first(pool)

    outcome = switch.use(account.id, pool)

    # No close request, and no launch either: U-Pool did not take the editor
    # down, so starting one the user had deliberately quit is not its business.
    assert calls == ["running", "write_auth"]
    assert outcome.closed_cursor is False
    assert outcome.relaunched is False
    assert outcome.result.warnings == []
    assert outcome.result.files == [str(db)]
    assert pool.current_id() == account.id


def test_a_running_cursor_is_closed_written_and_started_again(measured, db, pool, monkeypatch):
    calls = record(monkeypatch, running=True)
    account = first(pool)

    outcome = switch.use(account.id, pool)

    assert calls == ["running", f"close({process.CLOSE_TIMEOUT})", "write_auth", "launch"]
    assert outcome.closed_cursor is True
    assert outcome.relaunched is True
    assert outcome.result.warnings == []
    assert outcome.result.backups == [str(db) + ".backup"]
    assert pool.current_id() == account.id


def test_a_cursor_that_will_not_close_leaves_the_database_alone(measured, db, pool, monkeypatch):
    """The test this module exists for: a failed close writes nothing at all.

    Not the auth keys, and not the backup either - a switch that refused is a
    switch that left no trace, and a ``state.vscdb.backup`` appearing beside a
    database nobody wrote would misreport what happened.
    """
    calls = record(monkeypatch, running=True, closes=False)
    account = first(pool)
    before = rows(db)

    with pytest.raises(UPoolError, match="did not close"):
        switch.use(account.id, pool)

    assert calls.count("write_auth") == 0
    assert calls == ["running", f"close({process.CLOSE_TIMEOUT})"]
    assert rows(db) == before
    assert not db.with_name(db.name + ".backup").exists()
    assert pool.current_id() == ""
    assert CursorStore().current_id() == ""


def test_a_cursor_that_will_not_start_again_is_a_warning_not_a_failure(
    measured, db, pool, monkeypatch
):
    calls = record(monkeypatch, running=True, launches=False)
    account = first(pool)

    outcome = switch.use(account.id, pool)

    assert calls[-1] == "launch"
    assert outcome.closed_cursor is True
    assert outcome.relaunched is False
    assert outcome.result.warnings == [process.LAUNCH_FAILED_NOTE]
    # The switch itself happened, which is the half the warning is not about.
    assert outcome.result.files == [str(db)]
    assert pool.current_id() == account.id


def test_an_expired_account_is_refused_by_name(measured, db, pool, monkeypatch):
    calls = record(monkeypatch, running=True)
    account = first(pool)
    pool.data()["accounts"][0]["status"] = STATUS_EXPIRED

    with pytest.raises(UPoolError, match="a@example.com") as excinfo:
        switch.use(account.id, pool)

    assert "expired" in str(excinfo.value)
    # Refused before the editor was so much as looked at, let alone closed.
    assert calls == []
    assert pool.current_id() == ""


def test_an_account_with_no_cookie_is_refused(measured, db, pool, monkeypatch):
    """Only a hand-edited cursor.json can produce this, and writing it would sign
    the editor out rather than fail."""
    calls = record(monkeypatch, running=True)
    account = first(pool)
    pool.data()["accounts"][0]["token"] = ""

    with pytest.raises(UPoolError, match="no session cookie"):
        switch.use(account.id, pool)

    assert calls == []


def test_an_account_that_is_not_in_the_pool_is_refused(measured, db, pool, monkeypatch):
    calls = record(monkeypatch, running=True)

    with pytest.raises(UPoolError, match="no longer in the pool"):
        switch.use("nothing-by-that-id", pool)

    assert calls == []


def test_every_measured_key_gets_a_value(pool):
    """``_auth_values`` and ``vscdb.AUTH_KEYS`` are two halves of one measurement.

    They are written in different files and nothing but this test makes them agree.
    A key added to one and forgotten in the other is a switch that half-applies -
    which ``write_auth`` refuses outright in one direction, and silently leaves the
    previous account's value in place in the other.
    """
    assert set(switch._auth_values(first(pool))) == set(vscdb.AUTH_KEYS)


def test_an_unknown_name_or_plan_is_written_blank_rather_than_skipped(pool):
    """A refresh that never ran must not leave the previous account's identity.

    Skipping the key would keep whatever the last sign-in wrote, so the account
    menu would show the old person's name over the new person's token. Empty is
    the honest version, and Cursor refills it from the token on next start.
    """
    account = CursorAccount(user_id="user_09ZZ", token="eyJ-nine")
    values = switch._auth_values(account)

    assert values["cursorAuth/cachedEmail"] == ""
    assert values["cursorAuth/cachedScopedProfile"] == ""
    assert values["cursorAuth/stripeMembershipType"] == ""
    assert set(values) == set(vscdb.AUTH_KEYS)


def test_the_auth_id_is_read_out_of_the_token_not_rebuilt_from_the_cookie():
    """The JWT's ``sub`` is what the sign-in snapshot put in that key verbatim.

    The fallback assembles the same string from the cookie's user id, which held
    for every account measured - but a provider prefix other than ``auth0|`` would
    break it, so the token wins whenever it parses.
    """
    payload = base64.urlsafe_b64encode(b'{"sub":"github|user_07GG"}').rstrip(b"=").decode()
    account = CursorAccount(user_id="user_07GG", token=f"eyJhbGciOiJI.{payload}.signature")

    assert switch._auth_values(account)["glass.lastSignedInAuthId"] == "github|user_07GG"


def test_a_token_that_is_not_a_jwt_falls_back_to_the_cookie_user_id():
    account = CursorAccount(user_id="user_08HH", token="not-a-jwt-at-all")

    assert switch._auth_values(account)["glass.lastSignedInAuthId"] == "auth0|user_08HH"


def test_a_known_avatar_goes_into_the_profile_blob():
    """Cursor keeps the picture in the same blob as the name.

    Measured: writing the name alone round-tripped every other key byte for byte
    and blanked the account menu's picture. Both or neither.
    """
    account = CursorAccount(
        user_id="user_01AB", token="eyJ", name="Umut Jan", avatar="https://cdn/pic"
    )
    profile = switch._auth_values(account)["cursorAuth/cachedScopedProfile"]

    assert profile == '{"displayName":"Umut Jan","pictureUrl":"https://cdn/pic"}'


def test_an_unknown_avatar_leaves_the_key_out_rather_than_empty():
    """An absent key falls back to initials; an empty string is a URL Cursor loads."""
    account = CursorAccount(user_id="user_01AB", token="eyJ", name="Umut Jan")
    profile = switch._auth_values(account)["cursorAuth/cachedScopedProfile"]

    assert profile == '{"displayName":"Umut Jan"}'


def test_the_avatar_never_reaches_the_ui(pool):
    """It is stored to be written back into Cursor, not to be rendered here."""
    account = CursorAccount(user_id="user_01AB", token="eyJ", avatar="https://cdn/pic")

    assert "avatar" not in account.summary(active=False)
    assert account.to_dict()["avatar"] == "https://cdn/pic"


def test_the_two_token_keys_hold_the_same_credential(pool):
    """The snapshot found one 413-character JWT in both, and a cookie carries one
    token - there is no second credential to put in the second key."""
    values = switch._auth_values(first(pool))

    assert values["cursorAuth/accessToken"] == values["cursorAuth/refreshToken"] != ""


def test_a_machine_with_no_cursor_database_is_refused_before_the_close(
    measured, pool, monkeypatch
):
    """No ``db`` fixture. Refusing here rather than at the write is the difference
    between an unhelpful message and an unhelpful message with the user's editor
    closed behind it."""
    calls = record(monkeypatch, running=True)

    with pytest.raises(UPoolError, match="open Cursor once"):
        switch.use(first(pool).id, pool)

    assert calls == []


def test_the_backup_holds_the_database_as_it_was_before_the_write(measured, db, pool):
    """The real ``vscdb.write_auth``, so this is the ordering proof.

    A backup taken after the write would carry the new account, which is the one
    thing a backup must not do - undoing the switch is renaming this file back.
    """
    account = first(pool)

    outcome = switch.use(account.id, pool)

    live = rows(db)
    assert live["upoolTest/accessToken"] == "eyJ-first"
    assert live["upoolTest/cachedEmail"] == "a@example.com"
    # The unowned neighbour under the same prefix stayed exactly where it was.
    assert live[NEIGHBOUR] == "free"

    saved = rows(Path(outcome.result.backups[0]))
    assert "upoolTest/accessToken" not in saved
    assert saved == {NEIGHBOUR: "free"}


def test_switching_twice_moves_current_and_rewrites_only_the_owned_keys(measured, db, pool):
    one, two = pool.list_accounts()

    switch.use(one.id, pool)
    switch.use(two.id, pool)

    assert pool.current_id() == two.id
    assert CursorStore().current_id() == two.id
    assert rows(db) == {
        NEIGHBOUR: "free",
        "upoolTest/accessToken": "eyJ-second",
        "upoolTest/cachedEmail": "b@example.com",
    }


def test_a_switch_without_a_store_reads_the_one_on_disk(measured, db, pool, monkeypatch):
    """``pool`` is optional, and omitting it must not switch to a different pool."""
    record(monkeypatch, running=False)
    account = first(pool)

    outcome = switch.use(account.id)

    assert outcome.result.files == [str(db)]
    assert CursorStore().current_id() == account.id
