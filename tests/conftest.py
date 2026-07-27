from __future__ import annotations

import pytest

from upool.store import Store


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Redirect both the fake HOME and U-Pool's own state into tmp_path."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("UPOOL_FAKE_HOME", str(fake_home))
    monkeypatch.setenv("UPOOL_HOME", str(tmp_path / "state"))
    return fake_home


@pytest.fixture
def store() -> Store:
    return Store()
