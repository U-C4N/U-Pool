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


def test_import_live_returns_none_without_a_base_url(adapter, settings_path):
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    assert adapter.import_live() is None
