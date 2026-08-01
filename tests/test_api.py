from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from upool import autostart, paths, settings, winenv
from upool.adapters.claude_desktop import PREVIEW_WARNING
from upool.api import Api
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
