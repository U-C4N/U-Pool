from __future__ import annotations

import json

import pytest

from upool import backup, paths, settings


@pytest.fixture
def source(sandbox):
    path = sandbox / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"theme": "dark"}\n', encoding="utf-8")
    return path


def test_the_copy_lands_beside_the_original(source):
    target = backup.sidecar(source)
    assert target == source.with_name("settings.json.backup")
    assert target.read_text() == '{"theme": "dark"}\n'


def test_a_second_copy_overwrites_the_first(source):
    backup.sidecar(source)
    source.write_text('{"theme": "light"}\n', encoding="utf-8")
    target = backup.sidecar(source)

    assert target.read_text() == '{"theme": "light"}\n'
    # One copy, not a pile: the point of the sidecar is the file as it was a
    # minute ago, and a second name beside it would only be a second question.
    assert sorted(p.name for p in source.parent.iterdir()) == [
        "settings.json",
        "settings.json.backup",
    ]


def test_a_file_that_is_not_there_yet_has_nothing_to_copy(sandbox):
    missing = sandbox / ".claude" / "settings.json"
    assert backup.sidecar(missing) is None
    assert not missing.with_name("settings.json.backup").exists()


def test_nothing_at_all_is_written_while_backups_are_off(source):
    settings.update({"backup_enabled": False})

    assert backup.sidecar(source) is None
    assert backup.env_snapshot({"ANTHROPIC_BASE_URL": "https://relay.example.com"}) is None
    assert not source.with_name("settings.json.backup").exists()
    assert not paths.backup_dir().exists()


def test_the_environment_snapshot_lands_in_the_app_home():
    target = backup.env_snapshot(
        {"ANTHROPIC_BASE_URL": "https://relay.example.com", "ANTHROPIC_API_KEY": None}
    )
    assert target == paths.backup_dir() / "environment.backup.json"
    # ``None`` has to survive the round trip: it says the name was unset, so
    # putting it back by hand is a deletion rather than an empty string.
    assert json.loads(target.read_text()) == {
        "ANTHROPIC_BASE_URL": "https://relay.example.com",
        "ANTHROPIC_API_KEY": None,
    }


def test_an_environment_change_with_no_names_records_nothing():
    assert backup.env_snapshot({}) is None
    assert not paths.backup_dir().exists()
