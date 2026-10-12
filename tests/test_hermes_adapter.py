from __future__ import annotations

import json

import pytest
import yaml

from upool import claims, paths, settings, yamlio
from upool.adapters.hermes import MODEL_OVERRIDES_CLAIM, PROVIDERS_CLAIM, HermesAdapter
from upool.models import APP_HERMES, TRANSPORT_CHAT, Provider, UPoolError

# A config shaped like the real one: two providers the user wrote by hand, a
# selection pointing at one of them, and unrelated sections on either side.
LIVE = """\
model:
  default: moonshotai/kimi-k3-free
  provider: tokenrouter
  context_length: 200000
providers:
  hiyo:
    api: https://api.hiyo.top
    api_key: sk-hiyo
    default_model: claude-opus-4-8
    models:
      claude-opus-4-8:
        name: claude-opus-4-8
    name: hiyo
    transport: anthropic_messages
  tokenrouter:
    api: https://api.tokenrouter.io
    api_key: sk-router
    name: tokenrouter
    transport: chat_completions
fallback_providers: []
max_concurrent_sessions: 4
toolsets:
  default:
    - read
    - write
slack:
  bot_token: xoxb-secret
  channels:
    - general
timezone: ''
"""


def write_live(text: str = LIVE) -> None:
    path = paths.hermes_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_live() -> str:
    return paths.hermes_config_file().read_text(encoding="utf-8")


def relay(**kwargs) -> Provider:
    payload = {
        "app": APP_HERMES,
        "name": "Yunwu",
        "base_url": "https://api.yunwu.cloud",
        "api_key": "sk-yunwu",
        "model": "claude-sonnet-4-5",
    }
    payload.update(kwargs)
    return Provider(**payload)


def test_a_switch_leaves_every_other_section_byte_identical():
    write_live()
    before = read_live().splitlines(keepends=True)

    HermesAdapter().apply(relay())

    after = read_live().splitlines(keepends=True)
    # Only ``model`` and ``providers`` may move. Everything from the first line
    # after them onwards is the rest of the document and must be unchanged.
    tail = "".join(before[before.index("fallback_providers: []\n") :])
    assert "".join(after[after.index("fallback_providers: []\n") :]) == tail
    assert "bot_token: xoxb-secret" in read_live()


def test_a_switch_keeps_providers_the_user_wrote_by_hand():
    write_live()
    HermesAdapter().apply(relay())

    data = yamlio.read(paths.hermes_config_file())
    assert set(data["providers"]) == {"hiyo", "tokenrouter", "yunwu"}
    assert data["providers"]["hiyo"]["api_key"] == "sk-hiyo"
    assert data["model"]["provider"] == "yunwu"
    assert data["model"]["default"] == "claude-sonnet-4-5"
    # A key in ``model`` U-Pool does not own survives the rewrite of the section.
    assert data["model"]["context_length"] == 200000


def test_the_second_switch_removes_only_the_entry_the_first_one_wrote():
    write_live()
    adapter = HermesAdapter()
    adapter.apply(relay())
    result = adapter.apply(relay(name="Kimi", base_url="https://api.moonshot.cn/anthropic"))

    data = yamlio.read(paths.hermes_config_file())
    assert set(data["providers"]) == {"hiyo", "tokenrouter", "kimi"}
    assert result.removed == ["providers.yunwu"]
    assert claims.read(PROVIDERS_CLAIM) == ["kimi"]


def test_the_claim_is_what_decides_removal_not_the_file():
    """A provider U-Pool never wrote is not its to delete, whatever it is called.

    The record survives a restart, so an entry sharing a name with one U-Pool
    happens to be switching away from still belongs to whoever wrote it.
    """
    write_live()
    adapter = HermesAdapter()
    # ``hiyo`` is in the file but was never claimed.
    adapter.apply(relay(name="hiyo", base_url="https://api.yunwu.cloud"))
    adapter.apply(relay(name="Kimi", base_url="https://api.moonshot.cn/anthropic"))

    data = yamlio.read(paths.hermes_config_file())
    # It was claimed the moment U-Pool wrote it, so this time it does go - but the
    # entry left behind is the one U-Pool wrote, not the user's original.
    assert "kimi" in data["providers"]
    assert "tokenrouter" in data["providers"]


def test_the_official_provider_clears_the_selection_and_nothing_else():
    write_live()
    adapter = HermesAdapter()
    adapter.apply(relay())
    adapter.apply(Provider(app=APP_HERMES, name="Hermes Default", official=True))

    data = yamlio.read(paths.hermes_config_file())
    assert set(data["providers"]) == {"hiyo", "tokenrouter"}
    assert "provider" not in data["model"]
    assert data["model"]["context_length"] == 200000
    assert "bot_token: xoxb-secret" in read_live()


def test_a_file_that_will_not_parse_is_left_alone():
    write_live("model:\n  default: a\n  default: [unclosed\n")
    with pytest.raises(UPoolError, match="not valid YAML"):
        HermesAdapter().apply(relay())
    assert "unclosed" in read_live()


def test_missing_config_is_created_from_the_record():
    HermesAdapter().apply(relay())
    data = yamlio.read(paths.hermes_config_file())
    assert data["providers"]["yunwu"]["api"] == "https://api.yunwu.cloud"
    assert data["providers"]["yunwu"]["transport"] == "anthropic_messages"
    assert data["model"] == {"provider": "yunwu", "default": "claude-sonnet-4-5"}


def test_extra_keys_pass_through_and_managed_ones_do_not_duplicate():
    HermesAdapter().apply(relay(extra={"rate_limit_delay": "2", "api": "ignored"}))
    entry = yamlio.read(paths.hermes_config_file())["providers"]["yunwu"]
    assert entry["rate_limit_delay"] == "2"
    assert entry["api"] == "https://api.yunwu.cloud"


def test_model_metadata_hermes_filled_in_survives_a_re_switch():
    write_live()
    adapter = HermesAdapter()
    adapter.apply(relay())
    path = paths.hermes_config_file()
    data = yamlio.read(path)
    data["providers"]["yunwu"]["models"]["claude-sonnet-4-5"]["context_length"] = 200000
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    adapter.apply(relay(api_key="sk-rotated"))

    entry = yamlio.read(path)["providers"]["yunwu"]
    assert entry["api_key"] == "sk-rotated"
    assert entry["models"]["claude-sonnet-4-5"]["context_length"] == 200000


def test_import_live_reads_the_selected_provider():
    write_live()
    imported = HermesAdapter().import_live()
    assert imported is not None
    assert imported.name == "tokenrouter"
    assert imported.base_url == "https://api.tokenrouter.io"
    assert imported.api_key == "sk-router"
    assert imported.transport == "chat_completions"
    assert imported.model == "moonshotai/kimi-k3-free"


def test_import_live_returns_nothing_without_a_selection():
    write_live("providers:\n  hiyo:\n    api: https://api.hiyo.top\n")
    assert HermesAdapter().import_live() is None


def test_hermes_writes_no_environment_variables():
    assert HermesAdapter().env_namespace == ""
    assert HermesAdapter().env_vars(relay()) == {}


def test_a_sidecar_is_kept_before_the_first_write():
    write_live()
    settings.update({"backup_enabled": True})
    result = HermesAdapter().apply(relay())
    backup_path = paths.hermes_config_file().with_suffix(".yaml.backup")
    assert str(backup_path) in result.backups
    assert "tokenrouter" in backup_path.read_text(encoding="utf-8")


def proxy_profile(**kwargs) -> Provider:
    payload = {
        "name": "U-Pool API - chatgpt/auto",
        "base_url": "http://127.0.0.1:8318/v1",
        "model": "chatgpt/auto",
        "transport": TRANSPORT_CHAT,
        "extra": {"upool_proxy": "chatgpt"},
    }
    payload.update(kwargs)
    return relay(**payload)


def test_chatgpt_proxy_declares_missing_capabilities_for_the_resolved_custom_runtime():
    write_live()
    provider = proxy_profile()
    adapter = HermesAdapter()
    result = adapter.apply(provider)
    data = yamlio.read(paths.hermes_config_file())
    assert data["model"]["provider"] == provider.slug
    assert data["model_overrides"] == {"custom": {"chatgpt/auto": {
        "supports_reasoning": False, "supports_vision": False, "supports_tools": True,
    }}}
    assert "upool_proxy" not in data["providers"][provider.slug]
    fields = {tuple(json.loads(name)) for name in claims.read(MODEL_OVERRIDES_CLAIM)}
    assert fields == {("chatgpt/auto", key) for key in
                      ("supports_reasoning", "supports_vision", "supports_tools")}
    assert result.warnings == []
    adapter.apply(provider)
    assert {tuple(json.loads(name)) for name in claims.read(MODEL_OVERRIDES_CLAIM)} == fields


def test_proxy_capabilities_preserve_user_values_metadata_and_unrelated_models():
    write_live()
    path = paths.hermes_config_file()
    data = yamlio.read(path)
    metadata = {"supports_reasoning": False, "supports_vision": True,
                "supports_tools": False, "context_window": 12345, "user_metadata": {"owner": "user"}}
    overrides = {"custom": {"chatgpt/auto": metadata, "other-model": {"supports_reasoning": True}},
                 "another-provider": {"another-model": {"supports_tools": True}}}
    data["model_overrides"] = overrides
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    provider = proxy_profile()
    adapter = HermesAdapter()
    adapter.apply(provider)
    assert yamlio.read(path)["model_overrides"] == overrides
    assert claims.read(MODEL_OVERRIDES_CLAIM) == []
    data = yamlio.read(path)
    data["providers"][provider.slug]["models"][provider.model]["context_length"] = 54321
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    adapter.apply(provider)
    assert yamlio.read(path)["providers"][provider.slug]["models"][provider.model]["context_length"] == 54321
    adapter.apply(Provider(app=APP_HERMES, name="Official", official=True))
    assert yamlio.read(path)["model_overrides"] == overrides


@pytest.mark.parametrize("official", [False, True])
def test_switch_away_removes_only_claimed_capabilities_matching_the_values_written(official):
    write_live()
    path = paths.hermes_config_file()
    data = yamlio.read(path)
    data["model_overrides"] = {"custom": {
        "chatgpt/auto": {"context_window": 12345, "supports_vision": False},
        "other-model": {"supports_tools": True},
    }}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    adapter = HermesAdapter()
    adapter.apply(proxy_profile())
    fields = {tuple(json.loads(name)) for name in claims.read(MODEL_OVERRIDES_CLAIM)}
    assert fields == {("chatgpt/auto", "supports_reasoning"), ("chatgpt/auto", "supports_tools")}
    # A manual edit relinquishes that claimed field. Its new value survives.
    data = yamlio.read(path)
    data["model_overrides"]["custom"]["chatgpt/auto"]["supports_reasoning"] = True
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    target = Provider(app=APP_HERMES, name="Official", official=True) if official else relay()
    result = adapter.apply(target)
    assert yamlio.read(path)["model_overrides"] == {"custom": {
        "chatgpt/auto": {"context_window": 12345, "supports_vision": False, "supports_reasoning": True},
        "other-model": {"supports_tools": True},
    }}
    assert claims.read(MODEL_OVERRIDES_CLAIM) == []
    assert "model_overrides.custom.chatgpt/auto.supports_tools" in result.removed
    assert "model_overrides.custom.chatgpt/auto.supports_reasoning" not in result.removed


def test_claim_cleanup_does_not_treat_a_manual_integer_as_a_boolean():
    adapter = HermesAdapter()
    adapter.apply(proxy_profile())
    path = paths.hermes_config_file()
    data = yamlio.read(path)
    data["model_overrides"]["custom"]["chatgpt/auto"]["supports_vision"] = 0
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    adapter.apply(Provider(app=APP_HERMES, name="Official", official=True))
    assert yamlio.read(path)["model_overrides"] == {"custom": {"chatgpt/auto": {"supports_vision": 0}}}
    assert claims.read(MODEL_OVERRIDES_CLAIM) == []


def test_switch_to_another_proxy_model_drops_previous_owned_capabilities():
    adapter = HermesAdapter()
    adapter.apply(proxy_profile())
    adapter.apply(proxy_profile(name="Other U-Pool", model="chatgpt/other"))
    overrides = yamlio.read(paths.hermes_config_file())["model_overrides"]["custom"]
    assert set(overrides) == {"chatgpt/other"}
    assert {json.loads(name)[0] for name in claims.read(MODEL_OVERRIDES_CLAIM)} == {"chatgpt/other"}
    adapter.apply(Provider(app=APP_HERMES, name="Official", official=True))
    assert "model_overrides" not in yamlio.read(paths.hermes_config_file())


@pytest.mark.parametrize("effort", ["high", False, {"enabled": True, "effort": "medium"}])
def test_conflicting_reasoning_settings_warn_and_remain_unchanged(effort):
    write_live()
    path = paths.hermes_config_file()
    data = yamlio.read(path)
    data["agent"] = {"reasoning_effort": effort, "unrelated": "keep"}
    data["model_overrides"] = {"custom": {"chatgpt/auto": {"supports_reasoning": True}}}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    agent_text = read_live()[read_live().index("agent:\n"):]
    result = HermesAdapter().apply(proxy_profile())
    current = yamlio.read(path)
    assert current["agent"] == data["agent"]
    assert agent_text in read_live()
    assert current["model_overrides"]["custom"]["chatgpt/auto"]["supports_reasoning"] is True
    assert len(result.warnings) == 2
    assert all("omit" in warning for warning in result.warnings)
    assert all(json.loads(name)[1] != "supports_reasoning" for name in claims.read(MODEL_OVERRIDES_CLAIM))


@pytest.mark.parametrize("effort", [None, "", "   "])
def test_unset_global_reasoning_does_not_warn(effort):
    write_live("agent:\n  reasoning_effort: " + yaml.safe_dump(effort).splitlines()[0] + "\n")
    assert HermesAdapter().apply(proxy_profile()).warnings == []


@pytest.mark.parametrize("overrides", [["user-state"], {"custom": "user-state"},
                                        {"custom": {"chatgpt/auto": "user-state"}}])
def test_malformed_override_sections_are_preserved_and_warn(overrides):
    write_live(yaml.safe_dump({"model_overrides": overrides}, sort_keys=False))
    result = HermesAdapter().apply(proxy_profile())
    assert yamlio.read(paths.hermes_config_file())["model_overrides"] == overrides
    assert result.warnings and claims.read(MODEL_OVERRIDES_CLAIM) == []


def test_chatgpt_model_name_alone_does_not_claim_user_capabilities():
    HermesAdapter().apply(relay(model="chatgpt/auto", base_url="http://127.0.0.1:8318/v1"))
    assert "model_overrides" not in yamlio.read(paths.hermes_config_file())
    assert claims.read(MODEL_OVERRIDES_CLAIM) == []


@pytest.mark.parametrize("model", ["chatgpt/auto", "auto"])
@pytest.mark.parametrize("effort", ["high", "none", False, {"enabled": False}, {"effort": "medium"}])
def test_explicit_current_model_reasoning_override_warns_without_changing_agent(model, effort):
    agent = {"reasoning_effort": "", "reasoning_overrides": {model: effort, "unrelated-model": "high"}}
    write_live(yaml.safe_dump({"agent": agent}, sort_keys=False))
    agent_text = read_live()
    result = HermesAdapter().apply(proxy_profile())
    assert yamlio.read(paths.hermes_config_file())["agent"] == agent
    assert read_live().startswith(agent_text)
    assert len(result.warnings) == 1
    assert "agent.reasoning_overrides" in result.warnings[0] and "omitted" in result.warnings[0]


@pytest.mark.parametrize("effort", [None, "", "default", True, {}])
def test_unset_or_unrelated_per_model_reasoning_does_not_warn(effort):
    write_live(yaml.safe_dump({"agent": {"reasoning_overrides": {
        "chatgpt/auto": effort, "another-model": "high",
    }}}, sort_keys=False))
    assert HermesAdapter().apply(proxy_profile()).warnings == []
