from __future__ import annotations

from upool import atomicio, paths, settings


def test_defaults_apply_when_nothing_has_been_saved():
    assert settings.load() == {"launch_at_startup": False}


def test_update_round_trips_and_keeps_keys_it_does_not_know():
    atomicio.write_json(paths.settings_file(), {"written_by_a_newer_build": "keep me"})
    saved = settings.update({"launch_at_startup": True})
    assert saved["launch_at_startup"] is True
    assert saved["written_by_a_newer_build"] == "keep me"
    assert settings.load()["launch_at_startup"] is True


def test_a_damaged_file_falls_back_to_the_defaults():
    paths.settings_file().parent.mkdir(parents=True, exist_ok=True)
    paths.settings_file().write_text("{broken", encoding="utf-8")
    assert settings.load()["launch_at_startup"] is False


def test_a_string_flag_from_a_hand_edit_is_read_as_a_boolean():
    atomicio.write_json(paths.settings_file(), {"launch_at_startup": "true"})
    assert settings.load()["launch_at_startup"] is True
    atomicio.write_json(paths.settings_file(), {"launch_at_startup": "false"})
    assert settings.load()["launch_at_startup"] is False
