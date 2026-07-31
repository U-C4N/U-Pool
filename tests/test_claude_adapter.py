from __future__ import annotations

import json
import sys

import pytest

from upool import paths, settings, winenv
from upool.adapters.claude import ClaudeAdapter
from upool.models import APP_CLAUDE, AUTH_API_KEY, Provider, UPoolError

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="the environment key only exists on Windows"
)

# The shape this file actually has once Claude Code has been used for a while.
# Plugins, marketplaces, the theme, a status line, hooks and an allow-list are all
# the CLI's own state, and a provider record holds nothing that could rebuild them.
AUTHOR_SETTINGS = {
    "env": {"ANTHROPIC_BASE_URL": "https://old.example.com", "ANTHROPIC_AUTH_TOKEN": "sk-old"},
    "model": "opusplan",
    "effortLevel": "high",
    "theme": "dark-daltonized",
    "enabledPlugins": {"superpowers@obra-superpowers": True},
    "extraKnownMarketplaces": {
        "obra-superpowers": {"source": {"source": "github", "repo": "obra/superpowers"}}
    },
    "statusLine": {"type": "command", "command": "npx ccstatusline@latest"},
    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]},
    "permissions": {"allow": ["Bash(git status:*)", "WebFetch"], "deny": ["Bash(rm:*)"]},
}


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


def official() -> Provider:
    return provider(name="Claude Official", official=True, base_url="", api_key="")


def write(settings_path, data: dict) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data), encoding="utf-8")


def read(settings_path) -> dict:
    return json.loads(settings_path.read_text())


def test_apply_writes_env_and_strips_trailing_slash(adapter, settings_path):
    adapter.apply(provider())
    env = read(settings_path)["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://relay.example.com"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-token"
    assert "ANTHROPIC_API_KEY" not in env


def test_api_key_auth_style_uses_the_other_header(adapter, settings_path):
    adapter.apply(provider(auth_style=AUTH_API_KEY))
    env = read(settings_path)["env"]
    assert env["ANTHROPIC_API_KEY"] == "sk-token"
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_everything_claude_code_owns_survives_a_switch(adapter, settings_path):
    write(settings_path, AUTHOR_SETTINGS)
    result = adapter.apply(provider())
    data = read(settings_path)

    for key in ("model", "effortLevel", "theme", "enabledPlugins", "extraKnownMarketplaces"):
        assert data[key] == AUTHOR_SETTINGS[key], key
    assert data["statusLine"] == AUTHOR_SETTINGS["statusLine"]
    assert data["hooks"] == AUTHOR_SETTINGS["hooks"]
    assert data["permissions"] == AUTHOR_SETTINGS["permissions"]
    assert result.warnings == []


def test_an_env_name_u_pool_never_wrote_survives_a_switch(adapter, settings_path):
    # The block is shared. This machine's owner added HTTPS_PROXY by hand, and a
    # switch that rebuilt the block from the provider alone would delete it.
    write(settings_path, AUTHOR_SETTINGS | {"env": {"ANTHROPIC_BASE_URL": "x", "HTTPS_PROXY": "p"}})
    result = adapter.apply(provider())

    assert read(settings_path)["env"] == {
        "HTTPS_PROXY": "p",
        "ANTHROPIC_BASE_URL": "https://relay.example.com",
        "ANTHROPIC_AUTH_TOKEN": "sk-token",
    }
    assert result.removed == []


def test_a_hand_added_auth_token_is_replaced_because_that_is_what_a_switch_means(
    adapter, settings_path
):
    # The counterpart to the test above, and the line between them: HTTPS_PROXY is
    # nobody's business but the user's, while ANTHROPIC_AUTH_TOKEN is the thing the
    # switch exists to set. Leaving the old one beside the new endpoint is the
    # contradiction this release removed.
    write(settings_path, {"env": {"ANTHROPIC_AUTH_TOKEN": "sk-by-hand"}})
    adapter.apply(provider(auth_style=AUTH_API_KEY))

    env = read(settings_path)["env"]
    assert env["ANTHROPIC_API_KEY"] == "sk-token"
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_a_key_u_pool_does_not_own_is_never_reported_as_removed(adapter, settings_path):
    write(settings_path, AUTHOR_SETTINGS)
    assert adapter.apply(provider()).removed == []


def test_a_switch_never_leaves_the_previous_providers_env_behind(adapter, settings_path):
    adapter.apply(provider(name="First", extra={"FIRST_ONLY": "1"}))
    assert read(settings_path)["env"]["FIRST_ONLY"] == "1"

    adapter.apply(provider(name="Second", base_url="https://second.example.com", api_key="sk-two"))
    env = read(settings_path)["env"]
    assert "FIRST_ONLY" not in env
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-two"


def test_an_official_provider_removes_the_env_block_and_leaves_the_rest(adapter, settings_path):
    write(settings_path, AUTHOR_SETTINGS)
    adapter.apply(official())
    data = read(settings_path)

    assert "env" not in data
    assert data["theme"] == "dark-daltonized"
    assert data["permissions"] == AUTHOR_SETTINGS["permissions"]


def test_going_official_clears_the_anthropic_names_and_nothing_else(adapter, settings_path):
    # Handing control back to the vendor sign-in means removing what U-Pool set, not
    # emptying a block it shares. The proxy is the user's and has no bearing on which
    # account Claude Code signs in with.
    write(settings_path, {"env": {"ANTHROPIC_BASE_URL": "https://old", "HTTP_PROXY": "http://p:8080"}})
    result = adapter.apply(official())

    assert read(settings_path)["env"] == {"HTTP_PROXY": "http://p:8080"}
    assert result.removed == ["env.ANTHROPIC_BASE_URL"]


def test_missing_key_produces_a_warning(adapter):
    result = adapter.apply(provider(api_key=""))
    assert any("No API key" in w for w in result.warnings)


def test_an_unparsable_settings_file_refuses_the_switch(adapter, settings_path):
    write_back = "{oops"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(write_back, encoding="utf-8")

    with pytest.raises(UPoolError, match="not valid JSON"):
        adapter.apply(provider())
    # Keeping what we did not write means reading it first, and overwriting a file
    # that would not parse throws away every key in it.
    assert settings_path.read_text() == write_back
    assert not settings_path.with_name("settings.json.backup").exists()


def test_the_copy_beside_the_file_holds_it_as_it_was(adapter, settings_path):
    write(settings_path, AUTHOR_SETTINGS)
    result = adapter.apply(provider())
    sidecar = settings_path.with_name("settings.json.backup")

    assert str(sidecar) in result.backups
    assert json.loads(sidecar.read_text()) == AUTHOR_SETTINGS


def test_no_copy_is_kept_when_backups_are_switched_off(adapter, settings_path):
    write(settings_path, AUTHOR_SETTINGS)
    settings.update({"backup_enabled": False})
    result = adapter.apply(provider())

    assert result.backups == []
    assert not settings_path.with_name("settings.json.backup").exists()


def test_bypass_permissions_writes_the_default_mode(adapter, settings_path):
    adapter.apply(provider(bypass_permissions=True))
    assert read(settings_path)["permissions"]["defaultMode"] == "bypassPermissions"


def test_accept_edits_writes_the_narrower_mode(adapter, settings_path):
    adapter.apply(provider(accept_edits=True))
    assert read(settings_path)["permissions"]["defaultMode"] == "acceptEdits"


def test_bypass_wins_when_both_boxes_are_ticked(adapter, settings_path):
    adapter.apply(provider(bypass_permissions=True, accept_edits=True))
    assert read(settings_path)["permissions"]["defaultMode"] == "bypassPermissions"


def test_a_toggle_comes_and_goes_without_disturbing_the_allow_list(adapter, settings_path):
    write(settings_path, {"permissions": {"allow": ["Bash(git status:*)"]}})

    adapter.apply(provider(bypass_permissions=True))
    assert read(settings_path)["permissions"] == {
        "allow": ["Bash(git status:*)"],
        "defaultMode": "bypassPermissions",
    }

    result = adapter.apply(provider())
    assert read(settings_path)["permissions"] == {"allow": ["Bash(git status:*)"]}
    assert result.removed == ["permissions.defaultMode"]


def test_the_permissions_block_goes_away_with_the_mode_that_created_it(adapter, settings_path):
    adapter.apply(provider(bypass_permissions=True))
    adapter.apply(provider())
    # Nothing else was in there, and an empty block means nothing to Claude Code.
    assert "permissions" not in read(settings_path)


def test_skipping_the_bypass_dialog_is_owned_by_its_own_box(adapter, settings_path):
    adapter.apply(provider(bypass_permissions=True, skip_bypass_prompt=True))
    permissions = read(settings_path)["permissions"]
    assert permissions["skipDangerousModePermissionPrompt"] is True
    assert permissions["defaultMode"] == "bypassPermissions"

    # Ticked without bypass it is inert for Claude Code, but it is still the user's
    # setting, so the box - not the mode - decides whether it gets written.
    adapter.apply(provider(skip_bypass_prompt=True))
    permissions = read(settings_path)["permissions"]
    assert permissions["skipDangerousModePermissionPrompt"] is True
    assert "defaultMode" not in permissions

    adapter.apply(provider())
    assert "permissions" not in read(settings_path)


def test_a_permissions_value_of_the_wrong_shape_is_simply_replaced(adapter, settings_path):
    write(settings_path, {"permissions": "everything"})
    result = adapter.apply(provider(bypass_permissions=True))
    # A scalar is not something a surgical write can edit around, and it is not
    # ours to keep either, so it goes - reported, like any removal.
    assert read(settings_path)["permissions"] == {"defaultMode": "bypassPermissions"}
    assert result.removed == ["permissions"]


def test_project_mcp_toggle_is_a_top_level_boolean(adapter, settings_path):
    adapter.apply(provider(all_project_mcp=True))
    assert read(settings_path)["enableAllProjectMcpServers"] is True
    adapter.apply(provider())
    assert "enableAllProjectMcpServers" not in read(settings_path)


def test_official_provider_hands_the_permission_mode_back(adapter, settings_path):
    adapter.apply(provider(bypass_permissions=True, all_project_mcp=True))
    adapter.apply(official())
    data = read(settings_path)
    assert "permissions" not in data
    assert "enableAllProjectMcpServers" not in data


@windows_only
def test_the_same_values_reach_the_windows_environment(adapter):
    result = adapter.apply(provider(model="claude-sonnet-4-6"))

    assert set(result.env_written) == {
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
    }
    assert winenv.read("ANTHROPIC_BASE_URL") == "https://relay.example.com"
    assert winenv.read("ANTHROPIC_MODEL") == "claude-sonnet-4-6"


@windows_only
def test_an_auth_style_swap_takes_the_stale_name_out_of_the_environment(adapter):
    adapter.apply(provider())
    result = adapter.apply(provider(auth_style=AUTH_API_KEY))

    assert winenv.read("ANTHROPIC_API_KEY") == "sk-token"
    assert winenv.read("ANTHROPIC_AUTH_TOKEN") is None
    assert result.env_removed == ["ANTHROPIC_AUTH_TOKEN"]


@windows_only
def test_an_official_provider_clears_the_anthropic_namespace(adapter):
    adapter.apply(provider())
    result = adapter.apply(official())

    assert winenv.read("ANTHROPIC_BASE_URL") is None
    assert set(result.env_removed) == {"ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"}


@windows_only
def test_the_snapshot_holds_the_environment_as_it_was_before_the_switch(adapter):
    adapter.apply(provider(name="First", api_key="sk-one"))
    adapter.apply(provider(name="Second", api_key="sk-two"))
    snapshot = json.loads((paths.backup_dir() / "environment.backup.json").read_text())

    assert snapshot["ANTHROPIC_AUTH_TOKEN"] == "sk-one"


def test_import_live_picks_up_the_toggles(adapter, settings_path):
    write(
        settings_path,
        {
            "env": {"ANTHROPIC_BASE_URL": "https://relay.example.com"},
            "permissions": {
                "defaultMode": "bypassPermissions",
                "skipDangerousModePermissionPrompt": True,
            },
            "enableAllProjectMcpServers": True,
        },
    )
    imported = adapter.import_live()
    assert imported is not None
    assert imported.bypass_permissions is True
    assert imported.skip_bypass_prompt is True
    assert imported.accept_edits is False
    assert imported.all_project_mcp is True


def test_import_live_returns_none_without_a_base_url(adapter, settings_path):
    write(settings_path, {"theme": "dark"})
    assert adapter.import_live() is None


@windows_only
def test_import_live_falls_back_to_the_environment_when_the_file_has_no_env_block(
    adapter, settings_path
):
    # What this author's machine looks like: the setup lives in the environment and
    # settings.json says nothing about a provider at all.
    write(settings_path, {"theme": "dark"})
    winenv.apply(
        "anthropic",
        {"ANTHROPIC_BASE_URL": "https://env.example.com", "ANTHROPIC_AUTH_TOKEN": "sk-env"},
    )

    imported = adapter.import_live()
    assert imported is not None
    assert imported.base_url == "https://env.example.com"
    assert imported.api_key == "sk-env"
    assert "environment" in imported.note


@windows_only
def test_the_file_wins_over_the_environment_when_it_has_an_env_block(adapter, settings_path):
    write(settings_path, {"env": {"ANTHROPIC_BASE_URL": "https://from-file.example.com"}})
    winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": "https://from-env.example.com"})

    imported = adapter.import_live()
    assert imported is not None
    assert imported.base_url == "https://from-file.example.com"
