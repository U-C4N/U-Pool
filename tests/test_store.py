from __future__ import annotations

import json

import pytest

from upool import paths
from upool.models import APP_CLAUDE, APP_CODEX, Provider, UPoolError
from upool.store import Store


def make(app=APP_CLAUDE, **kwargs) -> Provider:
    defaults = {
        "name": "Relay",
        "base_url": "https://relay.example.com",
        "api_key": "sk-secret-value-1234",
    }
    defaults.update(kwargs)
    return Provider(app=app, **defaults)


def test_bootstrap_seeds_official_entry_per_app(store):
    for app in (APP_CLAUDE, APP_CODEX):
        providers = store.list_providers(app)
        assert len(providers) == 1
        assert providers[0].official
        assert store.current_id(app) == providers[0].id
    assert paths.config_file().exists()


def test_bootstrap_imports_existing_claude_setup(sandbox):
    settings = sandbox / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_BASE_URL": "https://api.kimi.com/coding",
                    "ANTHROPIC_AUTH_TOKEN": "sk-live",
                    "CUSTOM_FLAG": "1",
                },
                "theme": "dark",
            }
        ),
        encoding="utf-8",
    )

    store = Store()
    providers = store.list_providers(APP_CLAUDE)
    assert [p.name for p in providers] == ["Claude Official", "Imported"]
    imported = providers[1]
    assert imported.base_url == "https://api.kimi.com/coding"
    assert imported.api_key == "sk-live"
    assert imported.extra == {"CUSTOM_FLAG": "1"}
    # The imported provider is the one actually in use.
    assert store.current_id(APP_CLAUDE) == imported.id


def test_add_rejects_duplicate_names(store):
    store.add(make())
    with pytest.raises(UPoolError, match="already exists"):
        store.add(make())


def test_add_validates_url(store):
    with pytest.raises(UPoolError, match="http"):
        store.add(make(base_url="relay.example.com"))


def test_switch_marks_current_and_writes_live_file(store, sandbox):
    provider = store.add(make())
    result = store.switch(APP_CLAUDE, provider.id)

    assert store.current_id(APP_CLAUDE) == provider.id
    assert str(sandbox / ".claude" / "settings.json") in result.files
    env = json.loads((sandbox / ".claude" / "settings.json").read_text())["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://relay.example.com"


def test_delete_refuses_active_provider(store):
    provider = store.add(make())
    store.switch(APP_CLAUDE, provider.id)
    with pytest.raises(UPoolError, match="in use"):
        store.delete(APP_CLAUDE, provider.id)


def test_delete_removes_inactive_provider(store):
    provider = store.add(make())
    store.delete(APP_CLAUDE, provider.id)
    assert provider.id not in [p.id for p in store.list_providers(APP_CLAUDE)]


def test_duplicate_gets_unique_name_and_id(store):
    provider = store.add(make())
    copy = store.duplicate(APP_CLAUDE, provider.id)
    assert copy.id != provider.id
    assert copy.name == "Relay copy"
    assert copy.api_key == provider.api_key
    again = store.duplicate(APP_CLAUDE, provider.id)
    assert again.name == "Relay copy 2"


def test_updating_the_active_provider_rewrites_live_file(store, sandbox):
    provider = store.add(make())
    store.switch(APP_CLAUDE, provider.id)

    provider.base_url = "https://moved.example.com"
    store.update(provider)

    env = json.loads((sandbox / ".claude" / "settings.json").read_text())["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://moved.example.com"


def test_reorder_requires_the_same_id_set(store):
    first = store.add(make(name="A"))
    store.add(make(name="B", base_url="https://b.example.com"))
    with pytest.raises(UPoolError, match="did not match"):
        store.reorder(APP_CLAUDE, [first.id])


def test_reorder_persists_order(store):
    official = store.list_providers(APP_CLAUDE)[0]
    a = store.add(make(name="A"))
    b = store.add(make(name="B", base_url="https://b.example.com"))
    store.reorder(APP_CLAUDE, [b.id, a.id, official.id])
    assert [p.id for p in Store().list_providers(APP_CLAUDE)] == [b.id, a.id, official.id]


def test_state_survives_reload(store):
    provider = store.add(make())
    store.switch(APP_CLAUDE, provider.id)
    reloaded = Store()
    assert reloaded.current_id(APP_CLAUDE) == provider.id
    assert reloaded.get(APP_CLAUDE, provider.id).api_key == "sk-secret-value-1234"


def test_corrupt_config_is_reported_not_overwritten(store):
    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text("{not json", encoding="utf-8")
    with pytest.raises(UPoolError, match="not valid JSON"):
        Store().list_providers(APP_CLAUDE)
    assert paths.config_file().read_text() == "{not json"
