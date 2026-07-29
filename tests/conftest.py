from __future__ import annotations

import sys

import pytest

from upool import autostart
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
    """Redirect both the fake HOME and U-Pool's own state into tmp_path."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("UPOOL_FAKE_HOME", str(fake_home))
    monkeypatch.setenv("UPOOL_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(autostart, "RUN_KEY", TEST_RUN_KEY)
    monkeypatch.setattr(autostart, "APPROVED_KEY", TEST_APPROVED_KEY)
    _drop_test_registry_keys()
    yield fake_home
    _drop_test_registry_keys()


@pytest.fixture
def store() -> Store:
    return Store()
