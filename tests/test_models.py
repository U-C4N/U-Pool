from __future__ import annotations

import pytest

from upool.models import (
    APP_CLAUDE,
    APP_CLAUDE_DESKTOP,
    APP_CODEX,
    AUTH_API_KEY,
    SUPPORTED_APPS,
    Provider,
    UPoolError,
    as_bool,
    slugify,
    validate,
)


def test_the_three_apps_are_declared_in_tab_order():
    assert SUPPORTED_APPS == (APP_CLAUDE, APP_CLAUDE_DESKTOP, APP_CODEX)


def test_claude_desktop_is_validated_exactly_like_claude_code():
    draft = {"name": "Relay", "base_url": "https://relay.example.com"}
    # No assertion: the record taking the same shape is the point.
    validate(Provider(app=APP_CLAUDE_DESKTOP, auth_style=AUTH_API_KEY, **draft))
    with pytest.raises(UPoolError, match="auth style"):
        validate(Provider(app=APP_CLAUDE_DESKTOP, auth_style="basic", **draft))


def test_toggles_default_to_off():
    provider = Provider(app=APP_CLAUDE, name="Relay")
    assert provider.bypass_permissions is False
    assert provider.skip_bypass_prompt is False
    assert provider.accept_edits is False
    assert provider.all_project_mcp is False
    assert provider.bypass_approvals is False
    assert provider.web_search is False


def test_from_dict_coerces_the_shapes_json_and_hand_edits_produce():
    provider = Provider.from_dict(
        {
            "app": APP_CLAUDE,
            "name": "Relay",
            "bypass_permissions": "true",
            "accept_edits": 1,
            "all_project_mcp": "no",
            "web_search": "",
        }
    )
    assert provider.bypass_permissions is True
    assert provider.accept_edits is True
    assert provider.all_project_mcp is False
    assert provider.web_search is False


def test_from_dict_ignores_keys_the_dataclass_does_not_have():
    provider = Provider.from_dict({"app": APP_CLAUDE, "name": "Relay", "made_up_flag": True})
    assert not hasattr(provider, "made_up_flag")


def test_as_bool_reads_words_not_just_truthiness():
    assert as_bool("false") is False
    assert as_bool("False") is False
    assert as_bool("on") is True
    assert as_bool(None) is False


def test_slug_falls_back_when_a_name_has_nothing_usable():
    assert slugify("Kadirr.Dev v2") == "kadirr_dev_v2"
    assert slugify("...", fallback="abc123") == "abc123"
