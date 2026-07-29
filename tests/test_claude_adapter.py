from __future__ import annotations

import json

import pytest

from upool.adapters.claude import ClaudeAdapter
from upool.models import APP_CLAUDE, AUTH_API_KEY, Provider, UPoolError


@pytest.fixture
def adapter() -> ClaudeAdapter:
    return ClaudeAdapter()


@pytest.fixture
def settings_path(sandbox):
    return sandbox / ".claude" / "settings.json"


def provider(**kwargs) -> Provider:
    defaults = {
        "app": APP_CLAUDE,
        "name": "Relay",
        "base_url": "https://relay.example.com/",
        "api_key": "sk-token",
    }
    defaults.update(kwargs)
    return Provider(**defaults)


def test_apply_writes_env_and_strips_trailing_slash(adapter, settings_path):
    adapter.apply(provider())
    env = json.loads(settings_path.read_text())["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://relay.example.com"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-token"
    assert "ANTHROPIC_API_KEY" not in env


def test_api_key_auth_style_uses_the_other_header(adapter, settings_path):
    adapter.apply(provider(auth_style=AUTH_API_KEY))
    env = json.loads(settings_path.read_text())["env"]
    assert env["ANTHROPIC_API_KEY"] == "sk-token"
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_unrelated_settings_are_preserved(adapter, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"theme": "dark", "permissions": {"allow": ["Bash"]}, "env": {"KEEP": "yes"}}),
        encoding="utf-8",
    )
    adapter.apply(provider())
    data = json.loads(settings_path.read_text())
    assert data["theme"] == "dark"
    assert data["permissions"] == {"allow": ["Bash"]}
    assert data["env"]["KEEP"] == "yes"


def test_switching_clears_the_previous_providers_custom_env(adapter, settings_path):
    first = provider(name="First", extra={"FIRST_ONLY": "1"})
    adapter.apply(first)
    assert json.loads(settings_path.read_text())["env"]["FIRST_ONLY"] == "1"

    second = provider(name="Second", base_url="https://second.example.com", api_key="sk-two")
    adapter.apply(second, previous=first)
    env = json.loads(settings_path.read_text())["env"]
    assert "FIRST_ONLY" not in env
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-two"


def test_official_provider_removes_managed_env(adapter, settings_path):
    live = provider()
    adapter.apply(live)
    adapter.apply(provider(name="Claude Official", official=True, base_url="", api_key=""), previous=live)
    data = json.loads(settings_path.read_text())
    assert "env" not in data


def test_official_provider_keeps_user_env_vars(adapter, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"env": {"HTTP_PROXY": "http://proxy:8080"}}), encoding="utf-8")
    adapter.apply(provider(name="Claude Official", official=True, base_url="", api_key=""))
    assert json.loads(settings_path.read_text())["env"] == {"HTTP_PROXY": "http://proxy:8080"}


def test_missing_key_produces_a_warning(adapter):
    result = adapter.apply(provider(api_key=""))
    assert any("No API key" in w for w in result.warnings)


def test_malformed_settings_file_is_left_untouched(adapter, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{oops", encoding="utf-8")
    with pytest.raises(UPoolError, match="not valid JSON"):
        adapter.apply(provider())
    assert settings_path.read_text() == "{oops"


def test_switch_backs_up_the_previous_file(adapter, settings_path):
    adapter.apply(provider())
    result = adapter.apply(provider(name="Other", base_url="https://other.example.com"))
    assert result.backups
    backup_text = json.loads(open(result.backups[0], encoding="utf-8").read())
    assert backup_text["env"]["ANTHROPIC_BASE_URL"] == "https://relay.example.com"


def read(settings_path) -> dict:
    return json.loads(settings_path.read_text())


def test_bypass_permissions_writes_the_default_mode(adapter, settings_path):
    adapter.apply(provider(bypass_permissions=True))
    assert read(settings_path)["permissions"]["defaultMode"] == "bypassPermissions"


def test_accept_edits_writes_the_narrower_mode(adapter, settings_path):
    adapter.apply(provider(accept_edits=True))
    assert read(settings_path)["permissions"]["defaultMode"] == "acceptEdits"


def test_bypass_wins_when_both_boxes_are_ticked(adapter, settings_path):
    adapter.apply(provider(bypass_permissions=True, accept_edits=True))
    assert read(settings_path)["permissions"]["defaultMode"] == "bypassPermissions"


def test_unticking_the_box_removes_the_mode_but_keeps_the_rules(adapter, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"permissions": {"allow": ["Bash"], "defaultMode": "bypassPermissions"}}),
        encoding="utf-8",
    )
    adapter.apply(provider())
    assert read(settings_path)["permissions"] == {"allow": ["Bash"]}


def test_a_mode_u_pool_never_writes_is_left_alone(adapter, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"permissions": {"defaultMode": "plan"}}), encoding="utf-8")
    adapter.apply(provider())
    assert read(settings_path)["permissions"]["defaultMode"] == "plan"


def test_the_permissions_block_goes_away_with_the_mode_that_created_it(adapter, settings_path):
    adapter.apply(provider(bypass_permissions=True))
    adapter.apply(provider())
    assert "permissions" not in read(settings_path)


def test_skipping_the_bypass_dialog_is_owned_by_its_own_box(adapter, settings_path):
    adapter.apply(provider(bypass_permissions=True, skip_bypass_prompt=True))
    permissions = read(settings_path)["permissions"]
    assert permissions["skipDangerousModePermissionPrompt"] is True
    assert permissions["defaultMode"] == "bypassPermissions"

    # Ticked without bypass it is inert for Claude Code, but it is still the user's
    # setting: dropping it here would delete a key whose box is on.
    adapter.apply(provider(skip_bypass_prompt=True))
    permissions = read(settings_path)["permissions"]
    assert permissions["skipDangerousModePermissionPrompt"] is True
    assert "defaultMode" not in permissions

    adapter.apply(provider())
    assert "permissions" not in read(settings_path)


def test_a_permissions_value_that_is_not_an_object_is_left_alone(adapter, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"permissions": "everything"}), encoding="utf-8")
    result = adapter.apply(provider(bypass_permissions=True))
    assert read(settings_path)["permissions"] == "everything"
    assert any("not an object" in w for w in result.warnings)


def test_a_settings_file_that_forbids_bypass_mode_produces_a_warning(adapter, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"permissions": {"disableBypassPermissionsMode": "disable"}}), encoding="utf-8"
    )
    result = adapter.apply(provider(bypass_permissions=True))
    assert any("disableBypassPermissionsMode" in w for w in result.warnings)


def test_project_mcp_toggle_is_a_top_level_boolean(adapter, settings_path):
    adapter.apply(provider(all_project_mcp=True))
    assert read(settings_path)["enableAllProjectMcpServers"] is True
    adapter.apply(provider())
    assert "enableAllProjectMcpServers" not in read(settings_path)


def test_official_provider_hands_the_permission_mode_back(adapter, settings_path):
    live = provider(bypass_permissions=True, all_project_mcp=True)
    adapter.apply(live)
    adapter.apply(provider(name="Claude Official", official=True, base_url="", api_key=""), previous=live)
    data = read(settings_path)
    assert "permissions" not in data
    assert "enableAllProjectMcpServers" not in data


def test_import_live_picks_up_the_toggles(adapter, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "env": {"ANTHROPIC_BASE_URL": "https://relay.example.com"},
                "permissions": {
                    "defaultMode": "bypassPermissions",
                    "skipDangerousModePermissionPrompt": True,
                },
                "enableAllProjectMcpServers": True,
            }
        ),
        encoding="utf-8",
    )
    imported = adapter.import_live()
    assert imported is not None
    assert imported.bypass_permissions is True
    assert imported.skip_bypass_prompt is True
    assert imported.accept_edits is False
    assert imported.all_project_mcp is True


def test_import_live_returns_none_without_a_base_url(adapter, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    assert adapter.import_live() is None
