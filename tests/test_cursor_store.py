from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from upool import atomicio, paths
from upool.cursor.models import STATUS_EXPIRED, STATUS_OK, STATUS_UNKNOWN, CursorAccount
from upool.cursor.store import FACT_FIELDS, CursorStore
from upool.models import UPoolError


@pytest.fixture
def pool() -> CursorStore:
    return CursorStore()


def parsed(user_id: str = "user_01AB", token: str = "eyJ-first", **kwargs) -> CursorAccount:
    """What ``parse.py`` hands to :meth:`CursorStore.upsert` - a cookie, little else."""
    return CursorAccount(user_id=user_id, token=token, **kwargs)


def facts(**kwargs) -> SimpleNamespace:
    """A stand-in for ``api.AccountFacts``: every field optional, unset is ``None``.

    Built here rather than imported so this file tests the merge rule itself -
    "``None`` leaves the stored value alone" - and does not start failing because
    the client grew a field.
    """
    values = {name: None for name in FACT_FIELDS}
    values["message"] = None
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_a_fresh_install_has_an_empty_pool_and_writes_no_file(pool):
    """No file is the normal state until the first cookie is pasted."""
    assert pool.list_accounts() == []
    assert pool.current_id() == ""
    assert not paths.cursor_accounts_file().exists()


def test_accounts_survive_a_reload(pool):
    first, _ = pool.upsert(parsed())
    second, _ = pool.upsert(parsed(user_id="user_02CD", token="eyJ-second"))
    pool.merge_facts(first.id, facts(email="a@example.com", plan="Pro", usage_used=12.4))
    pool.set_current(second.id)

    reloaded = CursorStore()
    accounts = reloaded.list_accounts()
    assert [a.user_id for a in accounts] == ["user_01AB", "user_02CD"]
    assert accounts[0].email == "a@example.com"
    assert accounts[0].plan == "Pro"
    assert accounts[0].usage_used == 12.4
    # The credential is the whole point of the file; it has to come back intact.
    assert accounts[0].token == "eyJ-first"
    assert reloaded.current_id() == second.id


def test_a_corrupt_file_is_reported_not_overwritten(pool):
    paths.cursor_accounts_file().parent.mkdir(parents=True, exist_ok=True)
    paths.cursor_accounts_file().write_text("{not json", encoding="utf-8")

    with pytest.raises(UPoolError, match="not valid JSON") as excinfo:
        CursorStore().list_accounts()
    # Naming the file is what makes "move it aside" an instruction rather than
    # a suggestion - and every account in it is a credential, so it is never
    # replaced with a fresh one.
    assert paths.CURSOR_ACCOUNTS_FILE_NAME in str(excinfo.value)
    assert paths.cursor_accounts_file().read_text() == "{not json"


def test_upsert_of_a_known_user_id_refreshes_in_place(pool):
    """A rotated cookie for an account already in the pool must not duplicate it."""
    first, _ = pool.upsert(parsed())
    pool.upsert(parsed(user_id="user_02CD", token="eyJ-second"))
    pool.merge_facts(first.id, facts(name="Umut", usage_used=12.4, status=STATUS_EXPIRED))

    account, refreshed = pool.upsert(parsed(token="eyJ-rotated"))

    assert refreshed is True
    assert account.id == first.id
    assert [a.user_id for a in pool.list_accounts()] == ["user_01AB", "user_02CD"]
    # Position, name and last-known usage all stay; only the credential moves.
    assert account.token == "eyJ-rotated"
    assert account.name == "Umut"
    assert account.usage_used == 12.4
    # The 401 that greyed the row was about the old cookie, so the new one is
    # unjudged rather than dead on arrival.
    assert account.status == STATUS_UNKNOWN


def test_upsert_of_an_unchanged_cookie_keeps_the_last_verdict(pool):
    """Re-pasting the same dead cookie must not quietly un-grey the row."""
    account, _ = pool.upsert(parsed())
    pool.merge_facts(account.id, facts(status=STATUS_EXPIRED))

    again, refreshed = pool.upsert(parsed())

    assert refreshed is True
    assert again.status == STATUS_EXPIRED


def test_upsert_of_a_new_user_id_appends(pool):
    first, refreshed = pool.upsert(parsed())
    assert refreshed is False
    second, refreshed = pool.upsert(parsed(user_id="user_02CD", token="eyJ-second"))
    assert refreshed is False
    assert [a.id for a in pool.list_accounts()] == [first.id, second.id]
    assert first.id != second.id


def test_upsert_fills_a_blank_email_but_never_replaces_one(pool):
    """A CSV paste can complete a record the network has not answered for."""
    account, _ = pool.upsert(parsed())
    assert account.email == ""

    account, _ = pool.upsert(parsed(token="eyJ-rotated", email="csv@example.com"))
    assert account.email == "csv@example.com"

    pool.merge_facts(account.id, facts(email="real@example.com"))
    account, _ = pool.upsert(parsed(token="eyJ-again", email="csv@example.com"))
    assert account.email == "real@example.com"


def test_upsert_refuses_a_record_with_no_cookie(pool):
    with pytest.raises(UPoolError, match="user id or its token"):
        pool.upsert(parsed(token=""))
    with pytest.raises(UPoolError, match="user id or its token"):
        pool.upsert(parsed(user_id=""))
    assert pool.list_accounts() == []


def test_merge_facts_leaves_unset_fields_alone(pool):
    """Partial success fills partially: a failed usage call must not blank the bar."""
    account, _ = pool.upsert(parsed())
    pool.merge_facts(
        account.id,
        facts(email="a@example.com", plan="Pro", usage_used=12.4, usage_limit=20.0, status=STATUS_OK),
    )

    account = pool.merge_facts(account.id, facts(name="Umut", message="could not refresh"))

    assert account.name == "Umut"
    assert account.email == "a@example.com"
    assert account.plan == "Pro"
    assert account.usage_used == 12.4
    assert account.status == STATUS_OK
    # ``message`` is a property of the attempt, not of the account.
    assert "message" not in account.to_dict()


def test_merge_facts_stamps_last_checked_only_when_something_was_learned(pool):
    account, _ = pool.upsert(parsed())
    assert account.last_checked == 0

    account = pool.merge_facts(account.id, facts(message="could not refresh"))
    assert account.last_checked == 0

    account = pool.merge_facts(account.id, facts(status=STATUS_OK))
    assert account.last_checked > 0

    stamped = account.last_checked
    account = pool.merge_facts(account.id, facts(message="Cursor is down"))
    assert account.last_checked == stamped


def test_merge_facts_coerces_a_value_the_endpoint_invented(pool):
    """Every one of these fields comes from an endpoint that changes without notice."""
    account, _ = pool.upsert(parsed())
    account = pool.merge_facts(account.id, facts(status="suspended", usage_unit="tokens"))
    assert account.status == STATUS_UNKNOWN
    assert account.usage_unit == ""


def test_merge_facts_on_a_deleted_account_reports_it(pool):
    with pytest.raises(UPoolError, match="no longer in the pool"):
        pool.merge_facts("nothing", facts(status=STATUS_OK))


def test_delete_refuses_the_active_account(pool):
    account, _ = pool.upsert(parsed())
    pool.set_current(account.id)
    with pytest.raises(UPoolError, match="in use"):
        pool.delete(account.id)
    assert [a.id for a in pool.list_accounts()] == [account.id]


def test_delete_removes_an_inactive_account(pool):
    first, _ = pool.upsert(parsed())
    second, _ = pool.upsert(parsed(user_id="user_02CD", token="eyJ-second"))
    pool.set_current(first.id)

    pool.delete(second.id)

    assert [a.id for a in CursorStore().list_accounts()] == [first.id]


def test_delete_of_an_unknown_account_reports_it(pool):
    with pytest.raises(UPoolError, match="no longer in the pool"):
        pool.delete("nothing")


def test_reorder_requires_the_same_id_set(pool):
    first, _ = pool.upsert(parsed())
    pool.upsert(parsed(user_id="user_02CD", token="eyJ-second"))
    with pytest.raises(UPoolError, match="did not match"):
        pool.reorder([first.id])
    with pytest.raises(UPoolError, match="did not match"):
        pool.reorder([first.id, first.id])


def test_reorder_persists_order(pool):
    first, _ = pool.upsert(parsed())
    second, _ = pool.upsert(parsed(user_id="user_02CD", token="eyJ-second"))
    pool.reorder([second.id, first.id])
    assert [a.id for a in CursorStore().list_accounts()] == [second.id, first.id]


def test_set_current_rejects_an_account_that_is_gone(pool):
    with pytest.raises(UPoolError, match="no longer in the pool"):
        pool.set_current("nothing")
    assert pool.current_id() == ""


def test_a_hand_edited_file_is_read_as_far_as_it_makes_sense():
    """The file is plain JSON the user can open, so one bad row must not cost the pool."""
    atomicio.write_json(
        paths.cursor_accounts_file(),
        {
            "version": "junk",
            # Names an account that is not in the list - the green dot would sit
            # on nothing.
            "current": "gone",
            "accounts": [
                {"id": "keep", "user_id": "user_01AB", "token": "eyJ-first", "status": "made-up"},
                "not an object",
            ],
        },
    )

    pool = CursorStore()
    accounts = pool.list_accounts()

    assert [a.id for a in accounts] == ["keep"]
    assert accounts[0].status == STATUS_UNKNOWN
    assert pool.current_id() == ""
    assert pool.data()["version"] == 1


def test_the_file_is_json_u_pool_can_read_back(pool):
    """Nothing exotic on disk: the shape is version, current and a flat list."""
    account, _ = pool.upsert(parsed())
    pool.set_current(account.id)

    raw = json.loads(paths.cursor_accounts_file().read_text(encoding="utf-8"))

    assert raw["version"] == 1
    assert raw["current"] == account.id
    assert [entry["user_id"] for entry in raw["accounts"]] == ["user_01AB"]
