from __future__ import annotations

import pytest

from upool import paths


def test_the_sandbox_fixture_redirects_every_live_file(sandbox):
    assert paths.claude_settings_file() == sandbox / ".claude" / "settings.json"
    assert paths.codex_config_file() == sandbox / ".codex" / "config.toml"
    assert paths.codex_auth_file() == sandbox / ".codex" / "auth.json"


def test_resolving_the_real_home_during_a_test_run_raises(monkeypatch):
    """The guard that stands where a live install got rewritten once already.

    Everything under ``home()`` is a file some other program is using. A test that
    loses the redirect - or a throwaway script run inside a test session - would
    write to the developer's own ``~/.claude``, and the first switch would point
    their CLI at whatever endpoint the fixture happened to name.
    """
    monkeypatch.delenv("UPOOL_FAKE_HOME")
    with pytest.raises(RuntimeError, match="reached the real home directory"):
        paths.home()


def test_the_guard_covers_the_helpers_built_on_top_of_home(monkeypatch):
    monkeypatch.delenv("UPOOL_FAKE_HOME")
    monkeypatch.delenv("UPOOL_HOME")
    for helper in (paths.claude_settings_file, paths.codex_config_file, paths.config_file):
        with pytest.raises(RuntimeError):
            helper()
