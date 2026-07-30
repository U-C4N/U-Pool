from __future__ import annotations

import json

import pytest
import tomlkit

from upool.adapters.codex import CodexAdapter
from upool.models import APP_CODEX, WIRE_CHAT, Provider


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


def test_comments_and_unrelated_keys_are_gone_and_reported(adapter, config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '# my notes\napproval_policy = "on-request"\n\n[tui]\ntheme = "dark"\n',
        encoding="utf-8",
    )
    result = adapter.apply(provider())
    text = config_path.read_text()
    assert "# my notes" not in text
    assert "approval_policy" not in text
    assert "tui" not in tomlkit.parse(text)
    assert set(result.removed) == {"approval_policy", "tui"}
    assert result.warnings == []


def test_only_the_selected_provider_table_is_written(adapter, config_path):
    # Two foreign tables and a foreign selection, none of them known to the store -
    # exactly the state that used to leave two providers layered in one file.
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'model_provider = "first"\n\n'
        "[model_providers.first]\n"
        'base_url = "https://first.example.com"\n\n'
        "[model_providers.second]\n"
        'base_url = "https://second.example.com"\n',
        encoding="utf-8",
    )
    result = adapter.apply(provider(name="Third"))

    providers = tomlkit.parse(config_path.read_text())["model_providers"]
    assert list(providers) == ["third"]
    assert set(result.removed) == {"model_providers.first", "model_providers.second"}


def test_reapplying_the_same_provider_is_idempotent(adapter, config_path):
    adapter.apply(provider())
    first = config_path.read_text()
    result = adapter.apply(provider())
    assert config_path.read_text() == first
    assert result.removed == []


def test_auth_json_keeps_chatgpt_login_tokens(adapter, auth_path):
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps({"tokens": {"access_token": "abc"}}), encoding="utf-8")
    adapter.apply(provider())
    data = json.loads(auth_path.read_text())
    assert data["tokens"] == {"access_token": "abc"}
    assert data["OPENAI_API_KEY"] == "sk-token"


def test_official_provider_clears_selection_and_key(adapter, config_path, auth_path):
    adapter.apply(provider())
    adapter.apply(provider(name="OpenAI Official", official=True, base_url="", api_key=""))

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


def test_a_malformed_toml_file_is_replaced_rather_than_blocking(adapter, config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text("model_provider = [unclosed", encoding="utf-8")
    result = adapter.apply(provider())
    assert tomlkit.parse(config_path.read_text())["model_provider"] == "my_relay"
    assert result.removed == []
    assert result.backups  # the unparsable original is still recoverable


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


def test_toggles_off_means_the_keys_are_simply_absent(adapter, config_path):
    # Whatever was in the file - our own pair, a hand-picked policy, half a pair -
    # an unticked box writes nothing and a clean write keeps nothing.
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'approval_policy = "untrusted"\n'
        'sandbox_mode = "workspace-write"\n'
        'default_permissions = ":read-only"\n',
        encoding="utf-8",
    )
    result = adapter.apply(provider())
    doc = tomlkit.parse(config_path.read_text())
    assert "approval_policy" not in doc
    assert "sandbox_mode" not in doc
    assert "default_permissions" not in doc
    assert set(result.removed) == {"approval_policy", "sandbox_mode", "default_permissions"}


def test_web_search_uses_the_string_form_that_codex_actually_reads(adapter, config_path):
    adapter.apply(provider(web_search=True))
    doc = tomlkit.parse(config_path.read_text())
    # `[tools] web_search = true` parses but Codex throws the boolean away.
    assert doc["web_search"] == "live"
    assert "tools" not in doc

    adapter.apply(provider())
    assert "web_search" not in tomlkit.parse(config_path.read_text())


def test_bypass_cannot_clash_with_a_permission_profile_any_more(adapter, config_path):
    # ``default_permissions`` replaces ``sandbox_mode`` and Codex refuses both. It
    # can no longer survive the switch that would have contradicted it.
    config_path.parent.mkdir(parents=True)
    config_path.write_text('default_permissions = ":read-only"\n', encoding="utf-8")
    result = adapter.apply(provider(bypass_approvals=True))
    doc = tomlkit.parse(config_path.read_text())
    assert "default_permissions" not in doc
    assert doc["sandbox_mode"] == "danger-full-access"
    assert result.removed == ["default_permissions"]


def test_official_provider_hands_the_toggles_back(adapter, config_path):
    adapter.apply(provider(bypass_approvals=True, web_search=True))
    adapter.apply(provider(name="OpenAI Official", official=True, base_url="", api_key=""))
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


def test_import_live_still_needs_the_full_bypass_pair(adapter, config_path):
    # Only the pair is U-Pool's fingerprint, so half of it does not read back as
    # "bypass was on" - it is somebody else's narrower setting.
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'model_provider = "relay"\n'
        'sandbox_mode = "danger-full-access"\n\n'
        "[model_providers.relay]\n"
        'base_url = "https://relay.example.com/v1"\n',
        encoding="utf-8",
    )
    imported = adapter.import_live()
    assert imported is not None
    assert imported.bypass_approvals is False


def test_the_pre_clean_write_archive_is_kept_once(adapter, config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text('[mcp_servers.playwright]\ncommand = "npx"\n', encoding="utf-8")
    first = adapter.apply(provider())
    archives = [b for b in first.backups if b.endswith(".keep")]
    assert len(archives) == 1
    assert "mcp_servers" in open(archives[0], encoding="utf-8").read()

    second = adapter.apply(provider(name="Other", base_url="https://other.example.com"))
    assert not [b for b in second.backups if b.endswith(".keep")]
    assert "mcp_servers" in open(archives[0], encoding="utf-8").read()
