from __future__ import annotations

import json

import pytest

from upool import claims, paths, settings
from upool.adapters.opencode import PROVIDERS_CLAIM, OpenCodeAdapter
from upool.models import APP_OPENCODE, NPM_OPENAI_COMPATIBLE, Provider, UPoolError

# A config with things that are the user's: a theme, an MCP server, a plugin list
# and a provider they declared themselves.
LIVE = {
    "$schema": "https://opencode.ai/config.json",
    "theme": "tokyonight",
    "model": "mine/gpt-5",
    "provider": {
        "mine": {
            "npm": "@ai-sdk/openai",
            "name": "Mine",
            "options": {"baseURL": "https://api.openai.com/v1", "apiKey": "sk-mine"},
            "models": {"gpt-5": {"name": "gpt-5"}},
        }
    },
    "mcp": {"fs": {"type": "local", "command": ["mcp-fs"]}},
    "plugin": ["oh-my-opencode"],
    "keybinds": {"leader": "ctrl+x"},
}


def write_live(data: dict | str = LIVE) -> None:
    path = paths.opencode_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data, indent=2)
    path.write_text(text, encoding="utf-8")


def read_live() -> dict:
    return json.loads(paths.opencode_config_file().read_text(encoding="utf-8"))


def relay(**kwargs) -> Provider:
    payload = {
        "app": APP_OPENCODE,
        "name": "Yunwu",
        "base_url": "https://api.yunwu.cloud",
        "api_key": "sk-yunwu",
        "model": "claude-sonnet-4-5",
    }
    payload.update(kwargs)
    return Provider(**payload)


def test_a_switch_keeps_everything_the_user_owns():
    write_live()
    OpenCodeAdapter().apply(relay())

    data = read_live()
    assert data["theme"] == "tokyonight"
    assert data["mcp"] == {"fs": {"type": "local", "command": ["mcp-fs"]}}
    assert data["plugin"] == ["oh-my-opencode"]
    assert data["keybinds"] == {"leader": "ctrl+x"}
    # A provider they declared themselves is still there and still intact.
    assert data["provider"]["mine"]["options"]["apiKey"] == "sk-mine"


def test_the_switch_selects_the_provider_it_wrote():
    write_live()
    OpenCodeAdapter().apply(relay())

    data = read_live()
    entry = data["provider"]["yunwu"]
    assert data["model"] == "yunwu/claude-sonnet-4-5"
    assert entry["npm"] == "@ai-sdk/anthropic"
    assert entry["options"] == {
        "baseURL": "https://api.yunwu.cloud",
        "apiKey": "sk-yunwu",
    }
    assert entry["models"] == {"claude-sonnet-4-5": {"name": "claude-sonnet-4-5"}}


def test_the_second_switch_removes_only_the_entry_the_first_one_wrote():
    write_live()
    adapter = OpenCodeAdapter()
    adapter.apply(relay())
    result = adapter.apply(relay(name="Kimi", base_url="https://api.kimi.com/coding/v1"))

    data = read_live()
    assert set(data["provider"]) == {"mine", "kimi"}
    assert result.removed == ["provider.yunwu"]
    assert claims.read(PROVIDERS_CLAIM) == ["kimi"]
    assert data["model"] == "kimi/claude-sonnet-4-5"


def test_the_official_provider_clears_the_entry_and_the_selection():
    write_live()
    adapter = OpenCodeAdapter()
    adapter.apply(relay())
    adapter.apply(Provider(app=APP_OPENCODE, name="OpenCode Default", official=True))

    data = read_live()
    assert set(data["provider"]) == {"mine"}
    assert "model" not in data
    assert data["theme"] == "tokyonight"


def test_options_the_form_never_asks_for_survive_a_key_rotation():
    write_live()
    adapter = OpenCodeAdapter()
    adapter.apply(relay())
    path = paths.opencode_config_file()
    data = read_live()
    data["provider"]["yunwu"]["options"]["headers"] = {"x-relay": "1"}
    data["provider"]["yunwu"]["models"]["claude-sonnet-4-5"]["limit"] = {"context": 200000}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    adapter.apply(relay(api_key="sk-rotated"))

    entry = read_live()["provider"]["yunwu"]
    assert entry["options"]["apiKey"] == "sk-rotated"
    assert entry["options"]["headers"] == {"x-relay": "1"}
    assert entry["models"]["claude-sonnet-4-5"]["limit"] == {"context": 200000}


def test_extra_rows_become_sdk_options():
    OpenCodeAdapter().apply(relay(npm=NPM_OPENAI_COMPATIBLE, extra={"setCacheKey": "true"}))
    entry = read_live()["provider"]["yunwu"]
    assert entry["npm"] == NPM_OPENAI_COMPATIBLE
    assert entry["options"]["setCacheKey"] == "true"


def test_a_file_that_will_not_parse_is_left_alone():
    write_live("{ not json")
    with pytest.raises(UPoolError, match="not valid JSON"):
        OpenCodeAdapter().apply(relay())
    assert paths.opencode_config_file().read_text(encoding="utf-8") == "{ not json"


def test_missing_config_is_created_with_the_schema():
    OpenCodeAdapter().apply(relay())
    data = read_live()
    assert data["$schema"] == "https://opencode.ai/config.json"
    assert data["model"] == "yunwu/claude-sonnet-4-5"


def test_import_live_reads_the_selected_provider():
    write_live()
    imported = OpenCodeAdapter().import_live()
    assert imported is not None
    assert imported.name == "Mine"
    assert imported.base_url == "https://api.openai.com/v1"
    assert imported.api_key == "sk-mine"
    assert imported.model == "gpt-5"
    assert imported.npm == "@ai-sdk/openai"


def test_import_live_returns_nothing_without_a_selection():
    write_live({"provider": {"mine": {"npm": "@ai-sdk/openai"}}})
    assert OpenCodeAdapter().import_live() is None


def test_opencode_writes_no_environment_variables():
    assert OpenCodeAdapter().env_namespace == ""
    assert OpenCodeAdapter().env_vars(relay()) == {}


def test_a_sidecar_is_kept_before_the_first_write():
    write_live()
    settings.update({"backup_enabled": True})
    result = OpenCodeAdapter().apply(relay())
    backup_path = paths.opencode_config_file().with_suffix(".json.backup")
    assert str(backup_path) in result.backups
    assert "tokyonight" in backup_path.read_text(encoding="utf-8")
