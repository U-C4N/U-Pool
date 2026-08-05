from __future__ import annotations

import pytest

from upool import paths


def test_the_sandbox_fixture_redirects_every_live_file(sandbox):
    assert paths.claude_settings_file() == sandbox / ".claude" / "settings.json"
    assert paths.codex_config_file() == sandbox / ".codex" / "config.toml"
    assert paths.codex_auth_file() == sandbox / ".codex" / "auth.json"


def test_the_cursor_database_ignores_appdata_inside_the_sandbox(sandbox, monkeypatch):
    """%APPDATA% must lose to the fake home, the way %LOCALAPPDATA% does for Hermes.

    The real ``state.vscdb`` is 1.4 MB of the user's Cursor conversations plus
    their live MCP OAuth secrets, and it is open in the editor while the suite
    runs. ``cursor_app_dir()`` resolves through ``%APPDATA%`` on Windows, so
    without :func:`paths.sandboxed` winning here every Cursor test would write
    into that file rather than into tmp_path.
    """
    monkeypatch.setenv("APPDATA", r"C:\Users\someone-else\AppData\Roaming")
    monkeypatch.setenv("XDG_CONFIG_HOME", r"C:\Users\someone-else\.config")

    storage = sandbox / ".cursor-app" / "User" / "globalStorage"
    assert paths.cursor_state_db() == storage / "state.vscdb"
    assert paths.cursor_storage_json() == storage / "storage.json"


def test_the_cursor_account_file_lives_with_upools_own_state(sandbox):
    assert paths.cursor_accounts_file() == paths.app_home() / "cursor.json"
    assert paths.cursor_accounts_file() != paths.config_file()


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
