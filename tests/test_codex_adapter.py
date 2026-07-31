from __future__ import annotations

import json
import sys

import pytest
import tomlkit

from upool import paths, winenv
from upool.adapters.codex import CodexAdapter
from upool.models import APP_CODEX, WIRE_CHAT, Provider, UPoolError

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="the environment key only exists on Windows"
)

PROJECT_KEY = r"C:\Users\ACER\Documents\GitHub\U-Pool"

# A config.toml with a life of its own: MCP servers with nested env tables, a
# per-project trust level answered at a prompt, notify, and the Windows and shell
# policy blocks. None of it is U-Pool's, and none of it is in a provider record.
AUTHOR_CONFIG = r'''# Hand-written, and mostly none of U-Pool's business.
model = "gpt-5.1-codex-max"
model_provider = "codefast"
notify = ["powershell", "-c", "New-BurntToastNotification"]

[shell_environment_policy]
inherit = "all"
exclude = ["AWS_*"]

[windows]
wsl_default = false

[features]
web_search_request = true

[mcp_servers.node_repl]
command = "npx"
args = ["-y", "mcp-node-repl"]

[mcp_servers.node_repl.env]
NODE_OPTIONS = "--max-old-space-size=4096"

[projects.'C:\Users\ACER\Documents\GitHub\U-Pool']
trust_level = "trusted"

[model_providers.codefast]
name = "CodeFast"
base_url = "https://codefast.example.com/v1"
env_key = "codefast"
'''

# The shape a ChatGPT sign-in leaves behind - the one thing in auth.json U-Pool
# has no way of producing again.
LOGIN = {
    "OPENAI_API_KEY": None,
    "tokens": {"access_token": "at-1", "refresh_token": "rt-1", "account_id": "acct_1"},
    "last_refresh": "2026-07-30T21:00:00Z",
}


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


def official() -> Provider:
    return provider(name="OpenAI Official", official=True, base_url="", api_key="")


def write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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


def test_everything_around_the_provider_block_survives_a_switch(adapter, config_path):
    write(config_path, AUTHOR_CONFIG)
    result = adapter.apply(provider())
    text = config_path.read_text()
    doc = tomlkit.parse(text)

    assert doc["notify"] == ["powershell", "-c", "New-BurntToastNotification"]
    assert doc["shell_environment_policy"]["exclude"] == ["AWS_*"]
    assert doc["windows"]["wsl_default"] is False
    assert doc["features"]["web_search_request"] is True
    assert doc["mcp_servers"]["node_repl"]["args"] == ["-y", "mcp-node-repl"]
    assert doc["mcp_servers"]["node_repl"]["env"]["NODE_OPTIONS"] == "--max-old-space-size=4096"
    assert doc["projects"][PROJECT_KEY]["trust_level"] == "trusted"
    assert "# Hand-written" in text
    assert result.warnings == []


def test_the_switch_still_happens_around_everything_it_preserves(adapter, config_path):
    write(config_path, AUTHOR_CONFIG)
    result = adapter.apply(provider())
    doc = tomlkit.parse(config_path.read_text())

    assert doc["model_provider"] == "my_relay"
    assert list(doc["model_providers"]) == ["my_relay"]
    # ``model`` is U-Pool's key as much as the selection is, and this provider
    # pins none - so the previous provider's model goes, and is reported going.
    assert set(result.removed) == {"model", "model_providers.codefast"}
    assert "model" not in doc


def test_only_the_selected_provider_table_is_written(adapter, config_path):
    # Two foreign tables and a foreign selection, none of them known to the store -
    # exactly the state that used to leave two providers layered in one file.
    write(
        config_path,
        'model_provider = "first"\n\n'
        "[model_providers.first]\n"
        'base_url = "https://first.example.com"\n\n'
        "[model_providers.second]\n"
        'base_url = "https://second.example.com"\n',
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


def test_a_key_that_did_not_change_keeps_its_trailing_comment(adapter, config_path):
    write(config_path, 'model = "gpt-5.1-codex-max"  # the one I actually pay for\n')
    adapter.apply(provider(model="gpt-5.1-codex-max"))
    assert "# the one I actually pay for" in config_path.read_text()


def test_auth_json_holds_only_the_key_when_codex_reads_it_from_there(adapter, auth_path):
    adapter.apply(provider())
    assert json.loads(auth_path.read_text()) == {"OPENAI_API_KEY": "sk-token"}


def test_a_custom_env_key_empties_auth_json_instead_of_leaving_a_name_behind(adapter, auth_path):
    write(auth_path, json.dumps({"OPENAI_API_KEY": "sk-stale", "codefast": "cf-older-build"}))
    result = adapter.apply(provider(env_key="codefast"))

    # Codex only ever reads OPENAI_API_KEY out of this file, and the key now
    # reaches it through the environment - so there is nothing to put here, and
    # nothing to warn about either.
    assert json.loads(auth_path.read_text()) == {}
    assert result.warnings == []


def test_an_official_provider_without_a_login_gets_an_empty_auth_json(adapter, auth_path):
    adapter.apply(provider())
    adapter.apply(official())
    assert json.loads(auth_path.read_text()) == {}


def test_a_chatgpt_login_survives_a_round_trip_through_a_relay(adapter, auth_path):
    write(auth_path, json.dumps(LOGIN))

    adapter.apply(provider())
    assert json.loads(auth_path.read_text()) == {"OPENAI_API_KEY": "sk-token"}
    assert json.loads(paths.codex_login_file().read_text()) == LOGIN

    adapter.apply(official())
    assert json.loads(auth_path.read_text()) == LOGIN


def test_official_provider_clears_selection_and_key(adapter, config_path, auth_path):
    adapter.apply(provider())
    adapter.apply(official())

    doc = tomlkit.parse(config_path.read_text())
    assert "model_provider" not in doc
    assert "model_providers" not in doc
    assert "OPENAI_API_KEY" not in json.loads(auth_path.read_text())


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


def test_an_unparsable_config_refuses_the_switch(adapter, config_path):
    write(config_path, "model_provider = [unclosed")

    with pytest.raises(UPoolError, match="not valid TOML"):
        adapter.apply(provider())
    # Nothing can be preserved out of a document that could not be read.
    assert config_path.read_text() == "model_provider = [unclosed"
    assert not config_path.with_name("config.toml.backup").exists()


def test_an_unparsable_auth_file_stops_the_switch_before_config_is_touched(
    adapter, config_path, auth_path
):
    write(config_path, 'model = "gpt-5.1-codex-max"\n')
    write(auth_path, "{oops")

    with pytest.raises(UPoolError, match="not valid JSON"):
        adapter.apply(provider())
    assert config_path.read_text() == 'model = "gpt-5.1-codex-max"\n'


def test_the_copies_beside_both_files_hold_them_as_they_were(adapter, config_path, auth_path):
    write(config_path, AUTHOR_CONFIG)
    write(auth_path, json.dumps({"OPENAI_API_KEY": "sk-old"}))
    result = adapter.apply(provider())

    assert config_path.with_name("config.toml.backup").read_text() == AUTHOR_CONFIG
    assert json.loads(auth_path.with_name("auth.json.backup").read_text()) == {
        "OPENAI_API_KEY": "sk-old"
    }
    assert {str(config_path) + ".backup", str(auth_path) + ".backup"} <= set(result.backups)


def test_import_live_reads_the_selected_provider(adapter, config_path, auth_path):
    write(
        config_path,
        'model = "glm-4.6"\n'
        'model_provider = "zhipu"\n\n'
        "[model_providers.zhipu]\n"
        'name = "Zhipu GLM"\n'
        'base_url = "https://open.bigmodel.cn/api/paas/v4"\n'
        'wire_api = "chat"\n'
        'env_key = "OPENAI_API_KEY"\n',
    )
    write(auth_path, json.dumps({"OPENAI_API_KEY": "sk-zhipu"}))

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


def test_an_unticked_box_removes_its_own_pair_and_nobody_elses_key(adapter, config_path):
    write(
        config_path,
        'approval_policy = "untrusted"\n'
        'sandbox_mode = "workspace-write"\n'
        'default_permissions = ":read-only"\n',
    )
    result = adapter.apply(provider())
    doc = tomlkit.parse(config_path.read_text())

    assert "approval_policy" not in doc
    assert "sandbox_mode" not in doc
    # ``default_permissions`` is the user's, and nothing U-Pool wrote contradicts
    # it here, so a surgical write has no reason to touch it.
    assert doc["default_permissions"] == ":read-only"
    assert set(result.removed) == {"approval_policy", "sandbox_mode"}


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
    write(config_path, 'default_permissions = ":read-only"\n')
    result = adapter.apply(provider(bypass_approvals=True))
    doc = tomlkit.parse(config_path.read_text())
    assert "default_permissions" not in doc
    assert doc["sandbox_mode"] == "danger-full-access"
    assert result.removed == ["default_permissions"]


def test_official_provider_hands_the_toggles_back(adapter, config_path):
    adapter.apply(provider(bypass_approvals=True, web_search=True))
    adapter.apply(official())
    doc = tomlkit.parse(config_path.read_text())
    assert "approval_policy" not in doc
    assert "sandbox_mode" not in doc
    assert "web_search" not in doc


def test_import_live_picks_up_the_toggles(adapter, config_path):
    write(
        config_path,
        'model_provider = "relay"\n'
        'approval_policy = "never"\n'
        'sandbox_mode = "danger-full-access"\n'
        'web_search = "live"\n\n'
        "[model_providers.relay]\n"
        'name = "Relay"\n'
        'base_url = "https://relay.example.com/v1"\n',
    )
    imported = adapter.import_live()
    assert imported is not None
    assert imported.bypass_approvals is True
    assert imported.web_search is True


def test_import_live_returns_none_when_no_provider_selected(adapter, config_path):
    write(config_path, 'model = "gpt-5-codex"\n')
    assert adapter.import_live() is None


def test_import_live_still_needs_the_full_bypass_pair(adapter, config_path):
    # Only the pair is U-Pool's fingerprint, so half of it does not read back as
    # "bypass was on" - it is somebody else's narrower setting.
    write(
        config_path,
        'model_provider = "relay"\n'
        'sandbox_mode = "danger-full-access"\n\n'
        "[model_providers.relay]\n"
        'base_url = "https://relay.example.com/v1"\n',
    )
    imported = adapter.import_live()
    assert imported is not None
    assert imported.bypass_approvals is False


@windows_only
def test_the_name_the_provider_table_gives_is_what_reaches_the_environment(adapter):
    # ``env_key = "codefast"`` is the whole reason the registry is a target: Codex
    # resolves that name against the environment and nothing else.
    result = adapter.apply(provider(env_key="codefast"))

    assert winenv.read("codefast") == "sk-token"
    assert winenv.read("OPENAI_BASE_URL") == "https://relay.example.com/v1"
    assert set(result.env_written) == {"codefast", "OPENAI_BASE_URL"}


@windows_only
def test_an_official_provider_clears_the_openai_namespace(adapter):
    adapter.apply(provider(env_key="codefast"))
    result = adapter.apply(official())

    assert winenv.read("codefast") is None
    assert set(result.env_removed) == {"codefast", "OPENAI_BASE_URL"}


@windows_only
def test_import_live_reads_a_custom_env_key_out_of_the_environment(adapter, config_path):
    write(
        config_path,
        'model_provider = "codefast"\n\n'
        "[model_providers.codefast]\n"
        'name = "CodeFast"\n'
        'base_url = "https://codefast.example.com/v1"\n'
        'env_key = "codefast"\n',
    )
    winenv.apply("openai", {"codefast": "cf-live"})

    imported = adapter.import_live()
    assert imported is not None
    assert imported.env_key == "codefast"
    assert imported.api_key == "cf-live"
