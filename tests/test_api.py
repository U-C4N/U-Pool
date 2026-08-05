from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

from upool import autostart, paths, settings, winenv
from upool.adapters.claude_desktop import PREVIEW_WARNING
from upool.api import Api
from upool.cursor import api as cursorapi
from upool.cursor.api import AccountFacts
from upool.models import (
    APP_CLAUDE,
    APP_CLAUDE_DESKTOP,
    APP_CODEX,
    APP_HERMES,
    APP_OPENCODE,
)

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="the environment key only exists on Windows"
)


def draft(**kwargs) -> dict:
    payload = {
        "app": APP_CLAUDE,
        "name": "Relay",
        "base_url": "https://relay.example.com",
        "api_key": "sk-secret-value-1234",
    }
    payload.update(kwargs)
    return payload


def cookie(user_id: str, token: str) -> str:
    """One ``WorkosCursorSessionToken`` line, in the shape a browser hands over."""
    return f"WorkosCursorSessionToken={user_id}%3A%3A{token}"


@pytest.fixture(autouse=True)
def no_cursor_network(monkeypatch):
    """No test in this file may reach cursor.com.

    ``bootstrap`` starts a refresh over every account in the pool, so a test that
    adds one and then bootstraps would send that cookie to the real endpoints.
    Most tests here leave the pool empty and never start a thread at all, but
    that is a property of the test rather than of the suite, and it is not
    something the next person to add a Cursor test should have to remember. The
    tests that are about the refresh replace this with their own answer.
    """
    monkeypatch.setattr(cursorapi, "fetch_many", lambda accounts, *args, **kwargs: [])


def test_bootstrap_returns_every_app_in_tab_order():
    data = Api().bootstrap()["data"]
    every_app = [APP_CLAUDE, APP_CLAUDE_DESKTOP, APP_CODEX, APP_HERMES, APP_OPENCODE]
    assert [app["id"] for app in data["apps"]] == every_app
    assert [app["label"] for app in data["apps"]] == [
        "Claude Code",
        "Claude Desktop",
        "Codex",
        "Hermes",
        "OpenCode",
    ]
    assert set(data["state"]) == set(every_app)
    assert data["state"][APP_CLAUDE]["providers"][0]["official"] is True


def test_list_never_leaks_the_api_key():
    api = Api()
    api.save_provider(draft())
    provider = [p for p in api.list_providers(APP_CLAUDE)["data"]["providers"] if p["name"] == "Relay"][0]
    assert "api_key" not in provider
    assert provider["api_key_masked"] == "sk-s******1234"
    assert provider["has_api_key"] is True


def test_get_provider_returns_the_key_for_editing():
    api = Api()
    created = api.save_provider(draft())["data"]["id"]
    detail = api.get_provider(APP_CLAUDE, created)["data"]
    assert detail["api_key"] == "sk-secret-value-1234"


def test_blank_key_on_edit_keeps_the_stored_one():
    api = Api()
    created = api.save_provider(draft())["data"]["id"]
    api.save_provider(draft(id=created, api_key="", note="renamed"))
    detail = api.get_provider(APP_CLAUDE, created)["data"]
    assert detail["api_key"] == "sk-secret-value-1234"
    assert detail["note"] == "renamed"


def test_errors_come_back_as_envelopes_not_exceptions():
    api = Api()
    response = api.save_provider(draft(base_url="not-a-url"))
    assert response == {"ok": False, "error": "Request URL must start with http:// or https://."}
    assert api.get_provider(APP_CLAUDE, "missing")["ok"] is False


def test_switch_reports_the_files_it_wrote(sandbox):
    api = Api()
    created = api.save_provider(draft())["data"]["id"]
    result = api.switch_provider(APP_CLAUDE, created)["data"]

    assert result["state"]["current"] == created
    assert str(sandbox / ".claude" / "settings.json") in result["files"]
    assert result["warnings"] == []
    env = json.loads((sandbox / ".claude" / "settings.json").read_text())["env"]
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-secret-value-1234"


def test_switch_surfaces_adapter_warnings():
    api = Api()
    created = api.save_provider(draft(app=APP_CLAUDE_DESKTOP, name="Desktop Relay"))["data"]["id"]
    result = api.switch_provider(APP_CLAUDE_DESKTOP, created)["data"]

    assert result["warnings"] == [PREVIEW_WARNING]
    assert result["files"] == []
    assert result["env_written"] == []
    assert result["state"]["current"] == created


@windows_only
def test_switch_reports_the_environment_names_it_changed():
    api = Api()
    relay = api.save_provider(draft())["data"]["id"]
    official = api.list_providers(APP_CLAUDE)["data"]["providers"][0]["id"]

    written = api.switch_provider(APP_CLAUDE, relay)["data"]
    assert set(written["env_written"]) == {"ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"}
    assert written["env_removed"] == []

    cleared = api.switch_provider(APP_CLAUDE, official)["data"]
    assert cleared["env_written"] == []
    assert set(cleared["env_removed"]) == {"ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"}


def test_environment_lists_the_names_the_active_provider_manages():
    api = Api()
    created = api.save_provider(draft())["data"]["id"]
    api.switch_provider(APP_CLAUDE, created)
    info = api.environment(APP_CLAUDE)["data"]

    assert info["namespace"] == "anthropic"
    assert [v["name"] for v in info["vars"]] == ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"]
    assert info["supported"] is (sys.platform == "win32")


def test_environment_says_claude_desktop_manages_nothing():
    assert Api().environment(APP_CLAUDE_DESKTOP)["data"] == {
        "supported": False,
        "namespace": "",
        "vars": [],
    }


@windows_only
def test_environment_masks_the_value_it_found():
    api = Api()
    created = api.save_provider(draft())["data"]["id"]
    api.switch_provider(APP_CLAUDE, created)
    token = [
        v for v in api.environment(APP_CLAUDE)["data"]["vars"] if v["name"] == "ANTHROPIC_AUTH_TOKEN"
    ][0]

    # The panel has to show that a variable is set, not what it holds.
    assert token["value_masked"] == "sk-s******1234"
    assert token["owned"] is True


@windows_only
def test_a_variable_u_pool_has_no_record_of_writing_is_flagged_as_someone_elses():
    api = Api()
    created = api.save_provider(draft())["data"]["id"]
    api.switch_provider(APP_CLAUDE, created)
    # The same values, no record of having written them: what a reinstall finds
    # on a machine where the variables are already set.
    paths.env_owned_file().unlink()

    assert [v["owned"] for v in api.environment(APP_CLAUDE)["data"]["vars"]] == [False, False]


def test_open_env_settings_is_refused_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert Api().open_env_settings() == {"ok": False, "error": winenv.UNSUPPORTED_NOTE}


def test_read_live_config_lists_every_managed_file():
    files = Api().read_live_config(APP_CODEX)["data"]
    assert [Path(f["path"]).name for f in files] == ["config.toml", "auth.json"]
    # Claude Desktop has no live file, so the panel has nothing to offer for it.
    assert Api().read_live_config(APP_CLAUDE_DESKTOP)["data"] == []


def test_test_provider_skips_official_entries():
    api = Api()
    official = api.list_providers(APP_CLAUDE)["data"]["providers"][0]
    result = api.test_provider(APP_CLAUDE, official["id"])["data"]
    assert result["status"] == "skipped"
    assert result["name"] == "Claude Official"


def test_open_external_rejects_non_http_urls():
    assert Api().open_external("file:///etc/passwd")["ok"] is False


def test_advanced_toggles_round_trip_through_save():
    api = Api()
    created = api.save_provider(
        draft(bypass_permissions=True, skip_bypass_prompt=True, all_project_mcp=True)
    )["data"]["id"]

    detail = api.get_provider(APP_CLAUDE, created)["data"]
    assert detail["bypass_permissions"] is True
    assert detail["skip_bypass_prompt"] is True
    assert detail["all_project_mcp"] is True
    assert detail["accept_edits"] is False

    # The list summary carries them too, so a row can be badged as wide open.
    row = [p for p in api.list_providers(APP_CLAUDE)["data"]["providers"] if p["id"] == created][0]
    assert row["bypass_permissions"] is True


def test_a_toggle_sent_as_a_string_is_still_a_boolean():
    api = Api()
    created = api.save_provider(draft(bypass_permissions="true"))["data"]["id"]
    assert api.get_provider(APP_CLAUDE, created)["data"]["bypass_permissions"] is True


def test_switching_to_a_wide_open_provider_writes_the_permission_mode(sandbox):
    api = Api()
    created = api.save_provider(draft(bypass_permissions=True))["data"]["id"]
    api.switch_provider(APP_CLAUDE, created)
    data = json.loads((sandbox / ".claude" / "settings.json").read_text())
    assert data["permissions"]["defaultMode"] == "bypassPermissions"


def test_settings_expose_the_startup_switch():
    data = Api().get_settings()["data"]
    assert set(data) == {
        "launch_at_startup",
        "autostart_supported",
        "autostart_blocked",
        "autostart_command",
        "autostart_detail",
        "backup_enabled",
        "update_check_enabled",
    }
    assert data["launch_at_startup"] is False
    assert data["autostart_supported"] is (sys.platform == "win32")


def test_the_backup_switch_round_trips():
    api = Api()
    assert api.get_settings()["data"]["backup_enabled"] is True
    assert api.set_backup_enabled(False)["data"]["backup_enabled"] is False
    assert settings.load()["backup_enabled"] is False
    # The bridge sends whatever the checkbox had, so a string is still an answer.
    assert api.set_backup_enabled("true")["data"]["backup_enabled"] is True


def test_switching_with_backups_off_writes_the_file_and_no_copy(sandbox):
    api = Api()
    api.set_backup_enabled(False)
    created = api.save_provider(draft())["data"]["id"]
    result = api.switch_provider(APP_CLAUDE, created)["data"]

    assert result["backups"] == []
    assert (sandbox / ".claude" / "settings.json").exists()
    assert not (sandbox / ".claude" / "settings.json.backup").exists()


def test_bootstrap_carries_the_settings():
    assert Api().bootstrap()["data"]["settings"]["launch_at_startup"] is False


def test_launch_at_startup_round_trips():
    api = Api()
    if not autostart.supported():
        assert api.set_launch_at_startup(True)["ok"] is False
        return
    assert api.set_launch_at_startup(True)["data"]["launch_at_startup"] is True
    assert settings.load()["launch_at_startup"] is True
    assert api.set_launch_at_startup(False)["data"]["launch_at_startup"] is False
    assert settings.load()["launch_at_startup"] is False


# ---------------------------------------------------------------------- cursor


def test_cursor_state_starts_empty_and_points_at_the_sandboxed_database(sandbox):
    state = Api().cursor_state()["data"]

    assert set(state) == {"accounts", "current", "busy", "running", "supported", "db_path"}
    assert state["accounts"] == []
    assert state["current"] == ""
    assert state["busy"] is False
    # conftest stubs the process probe: the suite must never ask about - let
    # alone close - the developer's own editor.
    assert state["running"] is False
    # No Cursor install inside the fake home, which is the honest answer for a
    # machine that has never run it.
    assert state["supported"] is False
    assert state["db_path"].startswith(str(sandbox))
    assert state["db_path"].endswith("state.vscdb")


def test_cursor_add_counts_what_it_read_and_what_it_could_not(sandbox):
    api = Api()
    paste = "\n".join(
        [
            "# Netscape HTTP Cookie File",
            cookie("user_01AB", "token-a"),
            cookie("user_02CD", "token-b"),
            "not a cookie at all",
        ]
    )
    first = api.cursor_add(paste)["data"]

    assert (first["added"], first["refreshed"], first["skipped"]) == (2, 0, 2)
    assert [a["user_id"] for a in first["state"]["accounts"]] == ["user_01AB", "user_02CD"]

    # A rotated cookie for an account already in the pool refreshes that row
    # rather than adding a second one, and it keeps the position it had.
    again = api.cursor_add(cookie("user_01AB", "token-a-rotated"))["data"]
    assert (again["added"], again["refreshed"], again["skipped"]) == (0, 1, 0)
    assert [a["user_id"] for a in again["state"]["accounts"]] == ["user_01AB", "user_02CD"]


def test_a_cursor_summary_never_carries_the_session_cookie():
    api = Api()
    state = api.cursor_add(cookie("user_01AB", "eyJ-the-whole-account"))["data"]["state"]

    row = state["accounts"][0]
    assert "token" not in row
    assert row["has_token"] is True
    assert "eyJ-the-whole-account" not in json.dumps(state)
    # And it really was stored - the assertion above is about the response, not
    # about the paste having been dropped on the floor.
    assert "eyJ-the-whole-account" in paths.cursor_accounts_file().read_text(encoding="utf-8")


def test_cursor_delete_answers_with_the_whole_state():
    api = Api()
    api.cursor_add(cookie("user_01AB", "token-a"))
    second = api.cursor_add(cookie("user_02CD", "token-b"))["data"]["state"]["accounts"][1]

    state = api.cursor_delete(second["id"])["data"]
    assert [a["user_id"] for a in state["accounts"]] == ["user_01AB"]
    assert api.cursor_delete(second["id"]) == {
        "ok": False,
        "error": "That account is no longer in the pool.",
    }


def test_cursor_reorder_answers_with_the_order_it_stored():
    api = Api()
    api.cursor_add("\n".join([cookie("user_01AB", "token-a"), cookie("user_02CD", "token-b")]))
    ids = [a["id"] for a in api.cursor_state()["data"]["accounts"]]

    state = api.cursor_reorder(list(reversed(ids)))["data"]
    assert [a["id"] for a in state["accounts"]] == list(reversed(ids))
    assert api.cursor_reorder(["not-an-id"])["ok"] is False


def test_a_refresh_with_nothing_to_refresh_starts_no_thread():
    # Which is why the rest of this suite can bootstrap freely: an empty pool
    # never spawns a worker that could outlive the test that started it.
    assert Api().cursor_refresh()["data"]["busy"] is False


def test_cursor_refresh_answers_at_once_and_fills_the_card_afterwards(monkeypatch):
    api = Api()
    api.cursor_add(cookie("user_01AB", "token-a"))
    gate = threading.Event()
    asked: list[str] = []

    def answer(accounts, *args, **kwargs):
        asked.extend(account.user_id for account in accounts)
        gate.wait(5)
        return [
            AccountFacts(
                account_id=accounts[0].id,
                status="ok",
                email="who@example.com",
                name="Umut Can",
                plan="Pro",
                usage_used=12.4,
                usage_limit=20.0,
                usage_unit="usd",
                usage_percent=62.0,
            )
        ]

    monkeypatch.setattr(cursorapi, "fetch_many", answer)

    started = api.cursor_refresh()["data"]
    assert started["busy"] is True
    # Answered before the network did, with the card exactly as it stood.
    assert started["accounts"][0]["email"] == ""

    gate.set()
    api.join_cursor_refresh()

    settled = api.cursor_state()["data"]
    assert asked == ["user_01AB"]
    assert settled["busy"] is False
    row = settled["accounts"][0]
    assert (row["email"], row["name"], row["plan"], row["status"]) == (
        "who@example.com",
        "Umut Can",
        "Pro",
        "ok",
    )
    assert (row["usage_used"], row["usage_limit"], row["usage_unit"]) == (12.4, 20.0, "usd")
    assert row["last_checked"] > 0
    assert "token" not in row


def test_refreshing_an_account_that_is_gone_is_an_envelope_not_a_crash():
    assert Api().cursor_refresh("no-such-account") == {
        "ok": False,
        "error": "That account is no longer in the pool.",
    }


def test_cursor_use_surfaces_a_refusal_instead_of_raising():
    api = Api()
    row = api.cursor_add(cookie("user_01AB", "token-a"))["data"]["state"]["accounts"][0]

    # The sandbox has no Cursor database, so the switch refuses before it touches
    # the editor - and the bridge renders that as an envelope rather than letting
    # it reach the webview. Which refusal fires is the switch's business; that one
    # arrives as {"ok": false} instead of an exception is this test's.
    refused = api.cursor_use(row["id"])
    assert refused["ok"] is False
    assert "open Cursor once" in refused["error"]
    assert api.cursor_state()["data"]["current"] == ""
    assert api.cursor_use("no-such-account")["ok"] is False


def test_cursor_use_spreads_the_switch_outcome_beside_the_state(monkeypatch):
    from upool.adapters.base import ApplyResult
    from upool.cursor import switch as cursorswitch
    from upool.cursor.switch import SwitchOutcome

    api = Api()
    row = api.cursor_add(cookie("user_01AB", "token-a"))["data"]["state"]["accounts"][0]

    def fake_use(account_id, pool=None):
        # Writing through the store it was handed is what proves the bridge
        # passed its own: a second store would record this on disk and leave the
        # state below still reporting nobody as current.
        pool.set_current(account_id)
        result = ApplyResult()
        result.files.append("state.vscdb")
        result.backups.append("state.vscdb.backup")
        result.warnings.append("Cursor could not be started from here.")
        return SwitchOutcome(result=result, closed_cursor=True, relaunched=False)

    monkeypatch.setattr(cursorswitch, "use", fake_use)
    data = api.cursor_use(row["id"])["data"]

    assert set(data) == {"state", "files", "backups", "warnings", "closed_cursor", "relaunched"}
    assert data["files"] == ["state.vscdb"]
    assert data["backups"] == ["state.vscdb.backup"]
    assert data["warnings"] == ["Cursor could not be started from here."]
    assert (data["closed_cursor"], data["relaunched"]) == (True, False)
    assert data["state"]["current"] == row["id"]
    assert data["state"]["accounts"][0]["active"] is True


def test_bootstrap_carries_the_cursor_pool():
    data = Api().bootstrap()["data"]
    assert set(data["cursor"]) == {
        "accounts",
        "current",
        "busy",
        "running",
        "supported",
        "db_path",
    }
    assert data["cursor"]["accounts"] == []


def test_bootstrap_starts_the_cursor_refresh_before_it_takes_the_snapshot(monkeypatch):
    api = Api()
    api.cursor_add(cookie("user_01AB", "token-a"))
    gate = threading.Event()

    def answer(accounts, *args, **kwargs):
        gate.wait(5)
        return []

    monkeypatch.setattr(cursorapi, "fetch_many", answer)
    try:
        # busy has to be true on first paint: the UI only polls while it is, so a
        # snapshot taken before the thread started would leave the cards on their
        # stored figures until someone pressed Refresh all.
        assert api.bootstrap()["data"]["cursor"]["busy"] is True
    finally:
        gate.set()
        api.join_cursor_refresh()
