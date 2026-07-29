from __future__ import annotations

import json

import pytest
import tomlkit

from upool.adapters.codex import CodexAdapter
from upool.models import APP_CODEX, WIRE_CHAT, Provider, UPoolError


@pytest.fixture
def adapter() -> CodexAdapter:
    return CodexAdapter()


@pytest.fixture
def config_path(sandbox):
    return sandbox / ".codex" / "config.toml"


@pytest.fixture
def auth_path(sandbox):
    return sandbox / ".codex" / "auth.json"


def provider(**kwargs) -> Provider:
    defaults = {
        "app": APP_CODEX,
        "name": "My Relay",
        "base_url": "https://relay.example.com/v1/",
        "api_key": "sk-token",
    }
    defaults.update(kwargs)
    return Provider(**defaults)


def test_apply_writes_provider_table_and_selects_it(adapter, config_path, auth_path):
    adapter.apply(provider(model="gpt-5-codex", wire_api=WIRE_CHAT))
    doc = tomlkit.parse(config_path.read_text())

    assert doc["model_provider"] == "my_relay"
    assert doc["model"] == "gpt-5-codex"
    entry = doc["model_providers"]["my_relay"]
    assert entry["name"] == "My Relay"
    assert entry["base_url"] == "https://relay.example.com/v1"
    assert entry["wire_api"] == "chat"
    assert entry["env_key"] == "OPENAI_API_KEY"
    assert json.loads(auth_path.read_text())["OPENAI_API_KEY"] == "sk-token"


def test_comments_and_unrelated_keys_survive(adapter, config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '# my notes\napproval_policy = "on-request"\n\n[tui]\ntheme = "dark"\n',
        encoding="utf-8",
    )
    adapter.apply(provider())
    text = config_path.read_text()
    assert "# my notes" in text
    assert 'approval_policy = "on-request"' in text
    assert tomlkit.parse(text)["tui"]["theme"] == "dark"


def test_switching_removes_the_previous_provider_table(adapter, config_path):
    first = provider(name="First")
    adapter.apply(first)
    second = provider(name="Second", base_url="https://second.example.com")
    adapter.apply(second, previous=first)

    providers = tomlkit.parse(config_path.read_text())["model_providers"]
    assert "first" not in providers
    assert "second" in providers


def test_auth_json_keeps_chatgpt_login_tokens(adapter, auth_path):
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps({"tokens": {"access_token": "abc"}}), encoding="utf-8")
    adapter.apply(provider())
    data = json.loads(auth_path.read_text())
    assert data["tokens"] == {"access_token": "abc"}
    assert data["OPENAI_API_KEY"] == "sk-token"


def test_official_provider_clears_selection_and_key(adapter, config_path, auth_path):
    live = provider()
    adapter.apply(live)
    adapter.apply(provider(name="OpenAI Official", official=True, base_url="", api_key=""), previous=live)

    doc = tomlkit.parse(config_path.read_text())
    assert "model_provider" not in doc
    assert "model_providers" not in doc
    assert "OPENAI_API_KEY" not in json.loads(auth_path.read_text())


def test_custom_env_key_warns_instead_of_writing_auth_json(adapter, auth_path):
    result = adapter.apply(provider(env_key="RELAY_API_KEY"))
    assert any("RELAY_API_KEY" in w for w in result.warnings)
    assert not auth_path.exists()


def test_extra_keys_land_in_the_provider_table(adapter, config_path):
    adapter.apply(provider(extra={"query_params": "", "request_max_retries": "3"}))
    entry = tomlkit.parse(config_path.read_text())["model_providers"]["my_relay"]
    assert entry["request_max_retries"] == "3"
    # Empty values are dropped rather than written as noise.
    assert "query_params" not in entry


def test_top_level_extra_keys_land_on_config_root(adapter, config_path):
    adapter.apply(
        provider(
            extra={
                "model_reasoning_effort": "high",
                "disable_response_storage": "true",
                "preferred_auth_method": "apikey",
                "requires_openai_auth": "true",
            }
        )
    )
    doc = tomlkit.parse(config_path.read_text())
    assert doc["model_reasoning_effort"] == "high"
    assert doc["disable_response_storage"] is True
    assert doc["preferred_auth_method"] == "apikey"
    entry = doc["model_providers"]["my_relay"]
    assert entry["requires_openai_auth"] is True
    assert "model_reasoning_effort" not in entry


def test_malformed_toml_is_left_untouched(adapter, config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text("model_provider = [unclosed", encoding="utf-8")
    with pytest.raises(UPoolError, match="not valid TOML"):
        adapter.apply(provider())
    assert config_path.read_text() == "model_provider = [unclosed"


def test_import_live_reads_the_selected_provider(adapter, config_path, auth_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'model = "glm-4.6"\n'
        'model_provider = "zhipu"\n\n'
        "[model_providers.zhipu]\n"
        'name = "Zhipu GLM"\n'
        'base_url = "https://open.bigmodel.cn/api/paas/v4"\n'
        'wire_api = "chat"\n'
        'env_key = "OPENAI_API_KEY"\n',
        encoding="utf-8",
    )
    auth_path.write_text(json.dumps({"OPENAI_API_KEY": "sk-zhipu"}), encoding="utf-8")

    imported = adapter.import_live()
    assert imported is not None
    assert imported.name == "Zhipu GLM"
    assert imported.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert imported.wire_api == "chat"
    assert imported.model == "glm-4.6"
    assert imported.api_key == "sk-zhipu"


def test_bypass_writes_the_two_keys_the_cli_flag_sets(adapter, config_path):
    adapter.apply(provider(bypass_approvals=True))
    doc = tomlkit.parse(config_path.read_text())
    assert doc["approval_policy"] == "never"
    assert doc["sandbox_mode"] == "danger-full-access"
    # Codex reads them from the root, never from the endpoint table.
    entry = doc["model_providers"]["my_relay"]
    assert "approval_policy" not in entry
    assert "sandbox_mode" not in entry


def test_unticking_bypass_takes_both_keys_back_out(adapter, config_path):
    adapter.apply(provider(bypass_approvals=True))
    adapter.apply(provider())
    doc = tomlkit.parse(config_path.read_text())
    assert "approval_policy" not in doc
    assert "sandbox_mode" not in doc


def test_a_hand_picked_policy_is_not_overruled(adapter, config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'approval_policy = "untrusted"\nsandbox_mode = "workspace-write"\n', encoding="utf-8"
    )
    adapter.apply(provider())
    doc = tomlkit.parse(config_path.read_text())
    assert doc["approval_policy"] == "untrusted"
    assert doc["sandbox_mode"] == "workspace-write"


def test_web_search_uses_the_string_form_that_codex_actually_reads(adapter, config_path):
    adapter.apply(provider(web_search=True))
    doc = tomlkit.parse(config_path.read_text())
    # `[tools] web_search = true` parses but Codex throws the boolean away.
    assert doc["web_search"] == "live"
    assert "tools" not in doc

    adapter.apply(provider())
    assert "web_search" not in tomlkit.parse(config_path.read_text())


def test_a_permission_profile_in_the_file_warns_instead_of_clashing(adapter, config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text('default_permissions = ":read-only"\n', encoding="utf-8")
    result = adapter.apply(provider(bypass_approvals=True))
    assert any("default_permissions" in w for w in result.warnings)


def test_official_provider_hands_the_toggles_back(adapter, config_path):
    live = provider(bypass_approvals=True, web_search=True)
    adapter.apply(live)
    adapter.apply(provider(name="OpenAI Official", official=True, base_url="", api_key=""), previous=live)
    doc = tomlkit.parse(config_path.read_text())
    assert "approval_policy" not in doc
    assert "sandbox_mode" not in doc
    assert "web_search" not in doc


def test_import_live_picks_up_the_toggles(adapter, config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'model_provider = "relay"\n'
        'approval_policy = "never"\n'
        'sandbox_mode = "danger-full-access"\n'
        'web_search = "live"\n\n'
        "[model_providers.relay]\n"
        'name = "Relay"\n'
        'base_url = "https://relay.example.com/v1"\n',
        encoding="utf-8",
    )
    imported = adapter.import_live()
    assert imported is not None
    assert imported.bypass_approvals is True
    assert imported.web_search is True


def test_import_live_returns_none_when_no_provider_selected(adapter, config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text('model = "gpt-5-codex"\n', encoding="utf-8")
    assert adapter.import_live() is None


def test_a_lone_hand_written_sandbox_mode_is_not_treated_as_ours(adapter, config_path):
    # Only the full pair is U-Pool's fingerprint; half of it belongs to the user.
    config_path.parent.mkdir(parents=True)
    config_path.write_text('sandbox_mode = "danger-full-access"\n', encoding="utf-8")
    adapter.apply(provider())
    doc = tomlkit.parse(config_path.read_text())
    assert doc["sandbox_mode"] == "danger-full-access"
    assert "approval_policy" not in doc
