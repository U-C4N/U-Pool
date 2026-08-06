from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

import pytest

from upool import paths
from upool.cursor import deeplogin, process, switch, vscdb
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
    """Two accounts, each holding a live session token.

    A ``session``-kind token because that is the one kind
    ``switch._ensure_session_token`` accepts as-is; anything else now sends the
    switch through the (unstubbed, network-reaching) deep-login exchange, which
    is exactly what the tests that want that path set up for themselves.
    """
    store = CursorStore()
    store.upsert(
        CursorAccount(user_id="user_01AB", token=jwt_of_type("session", "first"), email="a@example.com")
    )
    store.upsert(
        CursorAccount(user_id="user_02CD", token=jwt_of_type("session", "second"), email="b@example.com")
    )
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


def jwt_of_type(kind: str, tag: str = "") -> str:
    """A three-segment token whose payload declares ``type``: what the desktop
    stores as ``session`` and what a browser exports as ``web``.

    ``tag`` exists only so two same-kind tokens (the ``pool`` fixture's two
    accounts) come out distinguishable - useful when a test asserts which
    account's token ended up written.
    """
    claims = f'"type":"{kind}"' + (f',"tag":"{tag}"' if tag else "")
    payload = base64.urlsafe_b64encode(f"{{{claims}}}".encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.sig"


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


def test_an_account_with_no_cookie_is_refused(measured, db, pool, monkeypatch):
    """Only a hand-edited cursor.json can produce this: no session token and no
    web cookie behind it either, so there is nothing left to convert or revive.

    Folded into ``_ensure_session_token``'s case 3 along with a genuinely expired
    session - both are "nothing this process can do without a fresh paste" - so
    the message is the same one that case raises, not a dedicated "no cookie" line.
    """
    calls = record(monkeypatch, running=True)
    account = first(pool)
    pool.data()["accounts"][0]["token"] = ""

    with pytest.raises(UPoolError, match="expired"):
        switch.use(account.id, pool)

    assert calls == []


def test_a_web_cookie_is_converted_before_the_editor_is_touched(measured, db, pool, monkeypatch):
    """The bug the old refusal stopped is now stopped by conversion instead: a web
    cookie is exchanged for a session token, persisted, and only then does the
    switch go on to touch the editor."""
    calls = record(monkeypatch, running=True)
    account, _ = pool.upsert(CursorAccount(user_id="user_1", token=jwt_of_type("web")))
    minted = deeplogin.DeepLogin(user_id="user_1", token=jwt_of_type("session"))
    monkeypatch.setattr(switch.deeplogin, "exchange", lambda uid, wt, **k: minted)

    switch.use(account.id, pool)

    assert pool.get(account.id).token == minted.token  # upgraded and persisted
    # The switch ran all the way through, which it could only do once the
    # conversion above had already succeeded.
    assert calls == ["running", f"close({process.CLOSE_TIMEOUT})", "write_auth", "launch"]


def test_a_failed_exchange_leaves_the_editor_untouched(measured, db, pool, monkeypatch):
    """The load-bearing order this task exists to guarantee: a conversion that
    cannot reach cursor.com never gets as far as asking Cursor to close."""
    calls = record(monkeypatch, running=True)
    account, _ = pool.upsert(CursorAccount(user_id="user_1", token=jwt_of_type("web")))

    def _boom(uid, wt, **k):
        raise UPoolError("cursor.com said no")

    monkeypatch.setattr(switch.deeplogin, "exchange", _boom)

    with pytest.raises(UPoolError, match="cursor.com said no"):
        switch.use(account.id, pool)

    assert calls == []  # not even process.running() was asked


def test_a_live_session_token_needs_no_network(measured, db, pool, monkeypatch):
    """The common path - a hand-pasted or adopted session token - makes no call
    to cursor.com at all."""
    record(monkeypatch, running=False)
    account, _ = pool.upsert(CursorAccount(user_id="user_1", token=jwt_of_type("session")))

    def _forbidden(*a, **k):
        raise AssertionError("exchange must not be called for a live session token")

    monkeypatch.setattr(switch.deeplogin, "exchange", _forbidden)

    switch.use(account.id, pool)  # does not raise, makes no call

    assert pool.current_id() == account.id


def test_an_expired_session_with_no_cookie_is_refused(measured, db, pool, monkeypatch):
    """A bare session token that has expired, with no web cookie behind it, is
    exactly the case 0.8.0's blanket ``STATUS_EXPIRED`` refusal existed for."""
    calls = record(monkeypatch, running=True)
    account, _ = pool.upsert(CursorAccount(user_id="user_1", token=jwt_of_type("session")))
    pool.data()["accounts"][-1]["status"] = STATUS_EXPIRED

    with pytest.raises(UPoolError, match="expired"):
        switch.use(account.id, pool)

    assert calls == []


def test_an_expired_web_row_is_re_minted(measured, db, pool, monkeypatch):
    """Expired no longer means refused for a row that still has a cookie: the old
    code refused any ``KIND_WEB`` token outright, regardless of status. The new
    one re-mints it."""
    calls = record(monkeypatch, running=True)
    account, _ = pool.upsert(CursorAccount(user_id="user_1", token=jwt_of_type("web")))
    pool.data()["accounts"][-1]["status"] = STATUS_EXPIRED
    minted = deeplogin.DeepLogin(user_id="user_1", token=jwt_of_type("session"))
    monkeypatch.setattr(switch.deeplogin, "exchange", lambda uid, wt, **k: minted)

    switch.use(account.id, pool)

    assert pool.get(account.id).token == minted.token
    assert calls == ["running", f"close({process.CLOSE_TIMEOUT})", "write_auth", "launch"]


def test_a_session_token_is_not_mistaken_for_a_browser_cookie(measured, db, pool, monkeypatch):
    """The account signed into the desktop app carries a session token, and Use
    has to keep working for it - that is the whole switch."""
    record(monkeypatch, running=False)
    account = first(pool)
    pool.data()["accounts"][0]["token"] = jwt_of_type("session")

    # No refusal: the switch runs through to writing and setting current.
    switch.use(account.id, pool)
    assert pool.current_id() == account.id


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


def test_the_live_session_is_assembled_from_the_named_keys(db, pool, monkeypatch):
    """The round trip that ``Api._adopt_live_session`` depends on.

    Cursor stores the token bare and the identity in a separate row, so nothing
    in that database is a cookie. An earlier version ran these values through the
    paste parser and found nothing, every start.
    """
    # Deliberately not ``record`` - that stubs ``vscdb.write_auth``, and this test
    # is about a value surviving a real one.
    stored = CursorAccount(
        user_id="user_01AB",
        token="eyJhbGciOiJI.eyJzdWIiOiJnaXRodWJ8dXNlcl8wMUFCIn0.sig",
        email="a@example.com",
        name="Umut Jan",
        avatar="https://cdn/pic",
        plan="pro",
        plan_status="active",
    )
    vscdb.write_auth(switch._auth_values(stored), db)

    live = switch.live_account()

    assert live is not None
    assert live.user_id == "user_01AB"
    assert (live.email, live.name, live.avatar) == ("a@example.com", "Umut Jan", "https://cdn/pic")
    assert (live.plan, live.plan_status) == ("pro", "active")
    assert live.token == stored.token


def test_a_signed_out_cursor_has_no_live_session(db):
    """Signed out leaves the rows empty rather than absent, so "" is the whole test."""
    assert switch.live_account() is None


def test_a_provider_prefix_other_than_auth0_still_yields_the_cookie_user_id():
    """Measured on a real second account: ``github|user_…``, not ``auth0|``.

    The user id in the cookie is the subject without whichever prefix it carried,
    so stripping by ``|`` is right and matching on ``auth0|`` would not be.
    """
    payload = base64.urlsafe_b64encode(b'{"sub":"github|user_07GG"}').rstrip(b"=").decode()
    account = CursorAccount(user_id="user_07GG", token=f"eyJ.{payload}.sig", name="G")

    assert switch._auth_values(account)["glass.lastSignedInAuthId"] == "github|user_07GG"


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


def test_the_summary_carries_the_token_kind_not_the_token():
    """The card disables Use on a browser cookie, so it needs the kind - but the
    kind, never the token itself."""
    web = CursorAccount(user_id="u", token=jwt_of_type("web"))
    session = CursorAccount(user_id="u", token=jwt_of_type("session"))

    assert web.summary(active=False)["token_kind"] == "web"
    assert session.summary(active=False)["token_kind"] == "session"
    assert "token" not in web.summary(active=False)


def test_a_token_that_is_not_a_jwt_has_an_unknown_kind():
    """An unparsable token is not called web, so Use is not blocked on a guess -
    an adopted session or a hand-pasted one is trusted until proven otherwise."""
    assert CursorAccount(user_id="u", token="whatever").summary(active=False)["token_kind"] == "unknown"


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
    assert live["upoolTest/accessToken"] == jwt_of_type("session", "first")
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
        "upoolTest/accessToken": jwt_of_type("session", "second"),
        "upoolTest/cachedEmail": "b@example.com",
    }


def test_a_switch_without_a_store_reads_the_one_on_disk(measured, db, pool, monkeypatch):
    """``pool`` is optional, and omitting it must not switch to a different pool."""
    record(monkeypatch, running=False)
    account = first(pool)

    outcome = switch.use(account.id)

    assert outcome.result.files == [str(db)]
    assert CursorStore().current_id() == account.id
