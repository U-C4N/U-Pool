from __future__ import annotations

import sys

import pytest

from upool import atomicio, autostart, paths
from upool.store import Store

# The real value lives under HKCU\...\CurrentVersion\Run. Tests get their own
# key so a run can never add, change or delete the user's own startup entry.
TEST_RUN_KEY = r"Software\U-Pool-Tests\Run"
TEST_APPROVED_KEY = r"Software\U-Pool-Tests\StartupApproved"


def _drop_test_registry_keys() -> None:
    if sys.platform != "win32":
        return
    import winreg

    # Children before the parent: DeleteKey refuses a key that still has subkeys.
    for path in (TEST_RUN_KEY, TEST_APPROVED_KEY, r"Software\U-Pool-Tests"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Redirect both the fake HOME and U-Pool's own state into tmp_path.

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
    _drop_test_registry_keys()
    yield fake_home
    _drop_test_registry_keys()


@pytest.fixture
def store() -> Store:
    return Store()
