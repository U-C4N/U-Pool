from __future__ import annotations

import sys

import pytest

from upool import atomicio, autostart, paths, winenv
from upool.store import Store

# The real value lives under HKCU\...\CurrentVersion\Run. Tests get their own
# key so a run can never add, change or delete the user's own startup entry.
TEST_RUN_KEY = r"Software\U-Pool-Tests\Run"
TEST_APPROVED_KEY = r"Software\U-Pool-Tests\StartupApproved"
# The same protection for the environment, and it matters more here: HKCU\Environment
# is where this machine keeps MINIMAX_CN_API_KEY, OPENAI_API_KEY and codefast, and a
# switch writes to it. Without the redirect the suite edits the developer's own shell.
TEST_ENV_KEY = r"Software\U-Pool-Tests\Environment"


def _drop_test_registry_keys() -> None:
    if sys.platform != "win32":
        return
    import winreg

    # Children before the parent: DeleteKey refuses a key that still has subkeys.
    for path in (TEST_RUN_KEY, TEST_APPROVED_KEY, TEST_ENV_KEY, r"Software\U-Pool-Tests"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Redirect the fake HOME, U-Pool's own state and the registry keys into tmp_path.

    Every target a switch writes to has to be redirected together. ``UPOOL_HOME``
    carries the ownership file that decides what :mod:`upool.winenv` may delete, so
    it has to move with ``ENV_KEY``: a real claim pointed at a scratch key, or a
    scratch claim pointed at the real key, would each be worse than neither.

    Update checks are seeded off: ``Api.bootstrap`` fires one in the background,
    and no test may reach out to api.github.com. A test that wants the check has
    to turn it on, or pass ``force=True``.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("UPOOL_FAKE_HOME", str(fake_home))
    monkeypatch.setenv("UPOOL_HOME", str(tmp_path / "state"))
    atomicio.write_json(paths.settings_file(), {"update_check_enabled": False})
    monkeypatch.setattr(autostart, "RUN_KEY", TEST_RUN_KEY)
    monkeypatch.setattr(autostart, "APPROVED_KEY", TEST_APPROVED_KEY)
    monkeypatch.setattr(winenv, "ENV_KEY", TEST_ENV_KEY)
    _drop_test_registry_keys()
    _assert_sandboxed(fake_home)
    yield fake_home
    _drop_test_registry_keys()


def _assert_sandboxed(fake_home) -> None:
    """Prove the redirects took before any test is allowed to write.

    A refactor that renames one of these - a path helper that stops going through
    ``paths.home()``, a registry constant that moves - would silently point the
    suite at the developer's own install, and the first thing it would do is
    rewrite the ``settings.json`` their Claude Code is reading. That has happened
    once from a script run outside pytest; it must not become possible from inside.
    """
    for resolved in (paths.claude_settings_file(), paths.codex_config_file(), paths.config_file()):
        assert str(resolved).startswith(str(fake_home.parent)), (
            f"{resolved} escaped the sandbox - it is not under {fake_home.parent}"
        )
    assert winenv.ENV_KEY == TEST_ENV_KEY, "winenv.ENV_KEY still points at the real environment"


@pytest.fixture
def store() -> Store:
    return Store()
