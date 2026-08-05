from __future__ import annotations

import sqlite3

import pytest

from upool import paths
from upool.cursor import vscdb
from upool.models import UPoolError

# Stands in for the key set section 6 will measure. Every function in vscdb is
# written over AUTH_KEYS and none of them names a key, so these tests exercise the
# real code paths today and keep passing unchanged when the measured list lands.
#
# The names are obviously not Cursor's on purpose. Two plausible ``cursorAuth/*``
# keys sit in the unrelated rows below instead, which is what proves ownership is
# by exact key rather than by prefix.
FAKE_KEYS = (
    "upoolTest/accessToken",
    "upoolTest/refreshToken",
    "upoolTest/cachedEmail",
)

MCP_SECRET = "mcpOAuth.secret.W29hdXRoLWF0dGVtcHQ6MGY3NzE4ZTRd"
MCP_SECRET_VALUE = '"pkce-verifier-' + "x" * 260 + '"'

# Twenty-odd rows that must come back untouched. The first four are the ones that
# would fall to a careless claim: two live MCP OAuth secrets, a real neighbour
# under the same prefix U-Pool writes into, and the key Cursor 3.14.7 added that
# no existing switcher knows about.
UNRELATED_ITEMS: dict[str, object] = {
    MCP_SECRET: MCP_SECRET_VALUE,
    "mcpOAuth.secret.W3BsdWdpbi1zdXBhYmFzZV0gbWNwX2NsaWVudA": '{"client_secret":"live"}',
    "cursorAuth/stripeMembershipType": "free",
    "glass.lastSignedInAuthId": "auth-01ABCD",
    "workbench.panel.aichat.numberOfVisibleViews": "1",
    "workbench.activity.pinnedViewlets2": '[{"id":"workbench.view.explorer"}]',
    "colorThemeData": '{"id":"vs-dark","label":"Dark+"}',
    "memento/workbench.editors.files.textFileEditor": '{"textEditorViewState":[]}',
    "cursor.update.sendAuthHeaders": "false",
    "history.entries": '[{"resource":"file:///c:/repo/main.py"}]',
    "workbench.explorer.treeViewState": '{"focus":[],"selection":[]}',
    "aiSettings": '{"model":"claude-4.5-sonnet","temperature":0.2}',
    "telemetry.lastSessionDate": "Tue Aug 04 2026",
    "storage.serviceMachineId": "8f0a1b2c-3d4e-5f60-7182-93a4b5c6d7e8",
    "workbench.view.extensions.state.hidden": '[{"id":"ms-python"}]',
    "commandPalette.mru.cache": '{"usesLRU":true,"entries":[]}',
    "userDataProfiles": "[]",
    "notebook.cellToolbarLocation": '{"default":"right"}',
    "extensionsIdentifiers/enabled": '[{"id":"vscode.git"}]',
    "unicode.note": "Kullanıcı ayarları - 用户设置 - 🎯",
    "empty.string.row": "",
    # A row that is a real BLOB rather than TEXT, so a read or write that coerced
    # types on its way past would show up as a changed flavour.
    "binary.leftovers": b"\x00\x01\x02cursor\xff",
}


def seed(**owned: str) -> None:
    """Build a database shaped like the real one, with ``owned`` already in it.

    Faithful to what the live 1.38 MB file actually holds: the same three tables,
    the same ``ItemTable`` declaration - ``value`` is declared ``BLOB`` while every
    row on that machine stores TEXT - and the full ``composerHeaders`` column set.
    A synthetic schema would let a write pass here and fail on the only database
    that matters.
    """
    path = paths.cursor_state_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)"
        )
        connection.execute(
            "CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)"
        )
        connection.execute(
            "CREATE TABLE composerHeaders ("
            "composerId TEXT PRIMARY KEY, workspaceId TEXT, createdAt INTEGER, "
            "lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER, "
            "recency INTEGER, checkpointAt INTEGER, value TEXT)"
        )
        connection.executemany(
            "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
            list(UNRELATED_ITEMS.items()) + list(owned.items()),
        )
        connection.executemany(
            "INSERT INTO cursorDiskKV(key, value) VALUES (?, ?)",
            [
                ("composerData:c-1", '{"conversation":[{"text":"how do I ship this"}]}'),
                ("bubbleId:c-1:b-9", '{"type":1,"text":"' + "y" * 400 + '"}'),
                ("checkpointId:c-2", b"\x89PNG\r\n\x1a\n"),
            ],
        )
        connection.executemany(
            "INSERT INTO composerHeaders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("c-1", "ws-a", 1754300000000, 1754390000000, 0, 0, 7, 0, '{"name":"U-Pool"}'),
                ("c-2", "ws-a", 1754100000000, 1754110000000, 1, 0, 3, 1754110000000, "{}"),
                ("c-3", "ws-b", 1753000000000, 1753000900000, 0, 1, 1, 0, '{"name":"probe"}'),
            ],
        )
    connection.close()


def snapshot() -> dict[str, object]:
    """Every row of every table, with the SQLite type of each value beside it.

    Comparing ``typeof(value)`` as well as the value is what makes "byte-identical"
    mean it: a TEXT row rewritten as a BLOB holding the same bytes would otherwise
    compare equal.
    """
    connection = sqlite3.connect(paths.cursor_state_db())
    try:
        return {
            "schema": connection.execute(
                "SELECT name, sql FROM sqlite_master ORDER BY name"
            ).fetchall(),
            "ItemTable": connection.execute(
                "SELECT key, typeof(value), value FROM ItemTable ORDER BY key"
            ).fetchall(),
            "cursorDiskKV": connection.execute(
                "SELECT key, typeof(value), value FROM cursorDiskKV ORDER BY key"
            ).fetchall(),
            "composerHeaders": connection.execute(
                "SELECT * FROM composerHeaders ORDER BY composerId"
            ).fetchall(),
        }
    finally:
        connection.close()


def item_rows() -> dict[str, tuple[str, object]]:
    return {key: (kind, value) for key, kind, value in snapshot()["ItemTable"]}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(vscdb, "AUTH_KEYS", FAKE_KEYS)
    return FAKE_KEYS


def test_a_write_leaves_every_row_it_does_not_own_byte_identical():
    """The reason this module is allowed to exist.

    The database it writes into holds the user's entire Cursor history, their
    composer state and their live MCP OAuth secrets. So the test is not "the auth
    keys were written" - it is that after a write, every other row of all three
    tables is exactly the row that was there before, down to its SQLite type.
    """
    seed(**{"upoolTest/accessToken": "old-token"})
    before = snapshot()

    written = vscdb.write_auth(
        {
            "upoolTest/accessToken": "new-token",
            "upoolTest/refreshToken": "new-refresh",
            "upoolTest/cachedEmail": "second@example.com",
        }
    )
    after = snapshot()

    assert written == list(FAKE_KEYS)
    # Nothing outside the claim moved, in any table.
    assert after["cursorDiskKV"] == before["cursorDiskKV"]
    assert after["composerHeaders"] == before["composerHeaders"]
    assert after["schema"] == before["schema"]
    unowned_before = [row for row in before["ItemTable"] if row[0] not in FAKE_KEYS]
    unowned_after = [row for row in after["ItemTable"] if row[0] not in FAKE_KEYS]
    assert unowned_after == unowned_before
    # Including the four that a prefix sweep or a careless DELETE would have taken.
    rows = item_rows()
    assert rows[MCP_SECRET] == ("text", MCP_SECRET_VALUE)
    assert rows["cursorAuth/stripeMembershipType"] == ("text", "free")
    assert rows["glass.lastSignedInAuthId"] == ("text", "auth-01ABCD")
    assert rows["binary.leftovers"] == ("blob", UNRELATED_ITEMS["binary.leftovers"])
    # And the owned keys hold what was asked for.
    assert vscdb.read_auth() == {
        "upoolTest/accessToken": "new-token",
        "upoolTest/refreshToken": "new-refresh",
        "upoolTest/cachedEmail": "second@example.com",
    }


def test_a_write_stores_text_the_way_every_neighbouring_row_does():
    """``value`` is declared BLOB, and all 100 rows on the live machine are TEXT."""
    seed()
    vscdb.write_auth({"upoolTest/accessToken": "eyJhbGciOi"})
    assert item_rows()["upoolTest/accessToken"] == ("text", "eyJhbGciOi")


def test_writing_the_same_key_twice_replaces_it_rather_than_adding_a_row():
    seed(**{"upoolTest/accessToken": "first"})
    vscdb.write_auth({"upoolTest/accessToken": "second"})
    vscdb.write_auth({"upoolTest/accessToken": "third"})

    connection = sqlite3.connect(paths.cursor_state_db())
    try:
        count = connection.execute(
            "SELECT count(*) FROM ItemTable WHERE key = ?", ("upoolTest/accessToken",)
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 1
    assert vscdb.read_auth()["upoolTest/accessToken"] == "third"


def test_reading_answers_with_the_owned_keys_and_nothing_else():
    """The rest of ItemTable is not read at all, MCP secrets included."""
    seed(**{"upoolTest/accessToken": "t", "upoolTest/cachedEmail": "a@example.com"})
    # The absent owned key is simply missing rather than empty, so a caller can
    # tell "Cursor never wrote this" from "Cursor wrote it blank".
    assert vscdb.read_auth() == {
        "upoolTest/accessToken": "t",
        "upoolTest/cachedEmail": "a@example.com",
    }


def test_a_key_outside_the_claim_is_refused_rather_than_quietly_dropped():
    """A silent drop hands back a Cursor that will not sign in, with nothing to point at."""
    seed()
    before = snapshot()
    with pytest.raises(UPoolError, match="does not own"):
        vscdb.write_auth(
            {"upoolTest/accessToken": "fine", "cursorAuth/stripeMembershipType": "pro"}
        )
    # And the write was refused whole - the owned half did not land either.
    assert snapshot() == before


def test_an_unmeasured_key_list_refuses_the_write_instead_of_reporting_success(monkeypatch):
    """The state this module ships in, until the section 6 diff fills AUTH_KEYS.

    Writing nothing and returning success would put "Switched to that account" on
    screen for an editor that is still signed in as the other one.
    """
    monkeypatch.setattr(vscdb, "AUTH_KEYS", ())
    seed()
    before = snapshot()

    with pytest.raises(UPoolError, match="does not yet know"):
        vscdb.write_auth({"cursorAuth/accessToken": "guessed"})
    assert vscdb.read_auth() == {}
    assert vscdb.clear_auth() == []
    assert snapshot() == before


def test_a_value_that_is_not_text_is_refused():
    seed()
    with pytest.raises(UPoolError, match="not text"):
        vscdb.write_auth({"upoolTest/accessToken": 42})
    assert "upoolTest/accessToken" not in item_rows()


def test_clearing_blanks_the_owned_rows_without_deleting_them():
    seed(**{"upoolTest/accessToken": "live", "upoolTest/refreshToken": "live"})
    before = snapshot()

    cleared = vscdb.clear_auth()

    assert cleared == ["upoolTest/accessToken", "upoolTest/refreshToken"]
    rows = item_rows()
    assert rows["upoolTest/accessToken"] == ("text", "")
    assert rows["upoolTest/refreshToken"] == ("text", "")
    # The row count is the point: nothing was deleted, in this table or any other.
    assert len(rows) == len(before["ItemTable"])
    assert snapshot()["cursorDiskKV"] == before["cursorDiskKV"]
    assert vscdb.read_auth() == {"upoolTest/accessToken": "", "upoolTest/refreshToken": ""}


def test_clearing_does_not_add_a_row_cursor_never_had():
    seed(**{"upoolTest/accessToken": "live"})
    assert vscdb.clear_auth() == ["upoolTest/accessToken"]
    assert "upoolTest/refreshToken" not in item_rows()


def test_a_database_outside_cursors_own_directory_is_refused(tmp_path):
    """The guard fires on a resolution bug, not on anything a user typed.

    Nothing reaches this path from input, so the only way it can point somewhere
    unbounded is a path helper answering wrongly - and that is the failure that
    would aim an INSERT OR REPLACE at a file nobody chose.
    """
    seed()
    stranger = tmp_path / "elsewhere" / "state.vscdb"
    stranger.parent.mkdir()
    stranger.write_bytes(b"not a database")

    for call in (
        lambda: vscdb.read_auth(stranger),
        lambda: vscdb.write_auth({"upoolTest/accessToken": "x"}, stranger),
        lambda: vscdb.clear_auth(stranger),
    ):
        with pytest.raises(UPoolError, match="outside Cursor's own directory"):
            call()
    assert stranger.read_bytes() == b"not a database"


def test_the_cursor_directory_itself_is_never_the_target():
    with pytest.raises(UPoolError, match="outside Cursor's own directory"):
        vscdb.read_auth(paths.cursor_app_dir())


def test_a_database_cursor_still_has_open_is_reported_rather_than_forced(monkeypatch):
    """A lock means the close did not take, so the answer is to say so.

    Every way of writing past a busy SQLite writer is a way to damage a 1.38 MB
    file holding somebody's chat history, so none of them is used here.
    """
    seed(**{"upoolTest/accessToken": "live"})
    before = snapshot()
    # Zero rather than the shipped five seconds, so the test does not wait them out.
    monkeypatch.setattr(vscdb, "TIMEOUT", 0)

    holder = sqlite3.connect(paths.cursor_state_db())
    holder.isolation_level = None
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(UPoolError, match="Close Cursor and try again"):
            vscdb.write_auth({"upoolTest/accessToken": "new"})
        with pytest.raises(UPoolError, match="Close Cursor and try again"):
            vscdb.clear_auth()
    finally:
        holder.rollback()
        holder.close()

    assert snapshot() == before


def test_a_machine_without_cursor_reads_empty_and_refuses_to_write():
    assert vscdb.exists() is False
    assert vscdb.read_auth() == {}
    assert vscdb.clear_auth() == []
    with pytest.raises(UPoolError, match="is Cursor installed"):
        vscdb.write_auth({"upoolTest/accessToken": "x"})
    # Connecting would have created the file, and an empty database where Cursor
    # expects its own is not something U-Pool should be able to invent.
    assert not paths.cursor_state_db().exists()


def test_writing_nothing_is_a_no_op_rather_than_an_error():
    seed()
    before = snapshot()
    assert vscdb.write_auth({}) == []
    assert snapshot() == before


def test_exists_reports_the_database_the_other_calls_use():
    assert vscdb.exists() is False
    seed()
    assert vscdb.exists() is True


def test_a_blob_row_under_an_owned_key_is_read_as_text():
    """Cursor stores these as TEXT today; a future one storing a blob must not raise."""
    seed()
    connection = sqlite3.connect(paths.cursor_state_db())
    with connection:
        connection.execute(
            "INSERT OR REPLACE INTO ItemTable(key, value) VALUES (?, ?)",
            ("upoolTest/accessToken", b"blob-token"),
        )
    connection.close()
    assert vscdb.read_auth() == {"upoolTest/accessToken": "blob-token"}
