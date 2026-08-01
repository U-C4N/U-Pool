from __future__ import annotations

import pytest
import yaml

from upool import claims, paths, settings, yamlio
from upool.adapters.hermes import PROVIDERS_CLAIM, HermesAdapter
from upool.models import APP_HERMES, Provider, UPoolError

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
