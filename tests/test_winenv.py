from __future__ import annotations

import json
import sys

import pytest

from upool import paths, winenv

# ``ENV_KEY`` is redirected onto a scratch key by the sandbox fixture, so every
# write here lands under HKCU\Software\U-Pool-Tests and never in the developer's
# own environment. Off Windows there is no key to redirect and nothing to test.
pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the environment key only exists on Windows"
)

RELAY = "https://relay.example.com"


def hand_set(name: str, value: str) -> None:
    """Set a value the way the Windows dialog would - outside U-Pool entirely."""
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, winenv.ENV_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def value_kind(name: str) -> int:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, winenv.ENV_KEY, 0, winreg.KEY_QUERY_VALUE
    ) as key:
        return winreg.QueryValueEx(key, name)[1]


def test_a_write_is_readable_and_recorded_as_owned():
    result = winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY})
    assert result.written == ["ANTHROPIC_BASE_URL"]
    assert winenv.read("ANTHROPIC_BASE_URL") == RELAY
    assert winenv.owned("anthropic") == ["ANTHROPIC_BASE_URL"]
    # On disk, not just in memory: the next run of the app is what has to know.
    assert json.loads(paths.env_owned_file().read_text()) == {"anthropic": ["ANTHROPIC_BASE_URL"]}


def test_the_ownership_record_follows_the_redirected_app_home(tmp_path):
    # UPOOL_HOME moves with ENV_KEY, so the claim describes the scratch key it was
    # made against. A claim from a real run pointed at a scratch key would let a
    # test delete names it never wrote.
    winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY})
    assert paths.env_owned_file() == tmp_path / "state" / "env-owned.json"


def test_a_name_dropped_from_desired_is_deleted():
    winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY, "ANTHROPIC_AUTH_TOKEN": "sk-one"})
    result = winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY})

    assert result.removed == ["ANTHROPIC_AUTH_TOKEN"]
    assert winenv.read("ANTHROPIC_AUTH_TOKEN") is None
    assert winenv.owned("anthropic") == ["ANTHROPIC_BASE_URL"]


def test_a_name_u_pool_never_set_survives_the_namespace_being_cleared():
    # The one property the whole module exists for. This machine keeps
    # MINIMAX_CN_API_KEY in the same registry key U-Pool writes to, and an official
    # provider clears every name in the namespace.
    hand_set("MINIMAX_CN_API_KEY", "mm-live-key")
    winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY})
    result = winenv.apply("anthropic", {})

    assert winenv.read("MINIMAX_CN_API_KEY") == "mm-live-key"
    assert result.removed == ["ANTHROPIC_BASE_URL"]
    assert winenv.owned("anthropic") == []


def test_namespaces_do_not_reach_into_each_other():
    winenv.apply("openai", {"codefast": "cf-live"})
    winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY})
    winenv.apply("anthropic", {})

    assert winenv.read("codefast") == "cf-live"
    assert winenv.owned("openai") == ["codefast"]


def test_a_name_that_already_existed_is_reported_as_adopted():
    hand_set("ANTHROPIC_BASE_URL", "https://set-by-hand.example.com")
    result = winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY})

    assert result.adopted == ["ANTHROPIC_BASE_URL"]
    assert result.written == ["ANTHROPIC_BASE_URL"]


def test_a_name_already_ours_is_not_adopted_a_second_time():
    winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": "https://first.example.com"})
    assert winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY}).adopted == []


def test_a_name_deleted_outside_u_pool_is_dropped_from_the_claim_in_silence():
    winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY, "ANTHROPIC_AUTH_TOKEN": "sk-one"})
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, winenv.ENV_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.DeleteValue(key, "ANTHROPIC_AUTH_TOKEN")

    result = winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY})
    # Reporting a removal U-Pool did not perform would put a name in the switch
    # toast that nothing on this machine has seen for a while.
    assert result.removed == []
    assert winenv.owned("anthropic") == ["ANTHROPIC_BASE_URL"]


def test_preview_marks_a_departing_name_and_ignores_everyone_elses():
    hand_set("MINIMAX_CN_API_KEY", "mm-live-key")
    winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY, "ANTHROPIC_AUTH_TOKEN": "sk-one"})

    plan = winenv.preview("anthropic", {"ANTHROPIC_BASE_URL": RELAY})
    assert plan == {"ANTHROPIC_BASE_URL": RELAY, "ANTHROPIC_AUTH_TOKEN": None}


def test_a_value_holding_a_percent_sign_is_written_as_expandable():
    import winreg

    winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY, "CACHE_DIR": r"%LOCALAPPDATA%\relay"})
    assert value_kind("CACHE_DIR") == winreg.REG_EXPAND_SZ
    assert value_kind("ANTHROPIC_BASE_URL") == winreg.REG_SZ


def test_a_registry_that_refuses_a_write_comes_back_as_an_error(monkeypatch):
    def refuse(name: str, value: str) -> None:
        raise OSError("Access is denied")

    monkeypatch.setattr(winenv, "_write", refuse)
    result = winenv.apply("anthropic", {"ANTHROPIC_BASE_URL": RELAY})

    assert result.written == []
    assert "ANTHROPIC_BASE_URL" in result.error
    # A value that would not write is not claimed either, so the next switch does
    # not try to delete something U-Pool never managed to set.
    assert winenv.owned("anthropic") == []
