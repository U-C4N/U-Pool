from __future__ import annotations

import pytest

from upool import atomicio, paths
from upool.proxy.models import ProxyError
from upool.proxy.store import AccountStore


@pytest.mark.parametrize("payload", [
    {"version": 2, "port": 8318, "key": "", "accounts": []},
    {"version": 1, "port": True, "key": "", "accounts": []},
    {"version": 1, "port": 8318, "key": None, "accounts": []},
    {"version": 1, "port": 8318, "key": "", "accounts": [{"id": "a", "provider": "other"}]},
    {"version": 1, "port": 8318, "key": "", "accounts": [{"provider": "grok"}]},
    {"version": 1, "port": 8318, "key": "", "accounts": [
        {"id": "a", "provider": "grok"}, {"id": "a", "provider": "grok"},
    ]},
])
def test_invalid_storage_never_becomes_a_cached_valid_state(payload):
    target = paths.proxy_dir() / "accounts.json"
    atomicio.write_json(target, payload)
    original = target.read_bytes()
    store = AccountStore()
    for _ in range(2):
        with pytest.raises(ProxyError) as caught:
            store.key()
        assert caught.value.code == "storage_error"
        assert target.read_bytes() == original


def test_busy_state_is_recovered_without_mutating_the_file_on_read():
    target = paths.proxy_dir() / "accounts.json"
    atomicio.write_json(target, {
        "version": 1, "port": 8318, "key": "client-key",
        "accounts": [{"id": "a", "provider": "grok", "status": "busy"}],
    })
    original = target.read_bytes()
    assert AccountStore().get("a")["status"] == "ready"
    assert target.read_bytes() == original
