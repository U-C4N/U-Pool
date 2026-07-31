from __future__ import annotations

from upool import atomicio, paths, settings


def test_defaults_apply_when_nothing_has_been_saved():
    # The sandbox seeds a settings file to keep tests off the network, so this
    # has to look at a genuinely empty state.
    paths.settings_file().unlink()
    assert settings.load() == {
        "launch_at_startup": False,
        "backup_enabled": True,
        "update_check_enabled": True,
        "update_last_check": 0,
        "update_skipped_version": "",
        "update_last_seen_version": "",
    }


def test_the_backup_switch_round_trips():
    assert settings.load()["backup_enabled"] is True
    assert settings.update({"backup_enabled": False})["backup_enabled"] is False
    assert settings.load()["backup_enabled"] is False


def test_a_hand_edited_last_check_that_is_not_a_number_reads_as_zero():
    atomicio.write_json(paths.settings_file(), {"update_last_check": "yesterday"})
    assert settings.load()["update_last_check"] == 0


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
    # Backups decide whether a file is copied before it is overwritten, so the
    # string form has to read as a boolean here too rather than as truthiness.
    atomicio.write_json(paths.settings_file(), {"backup_enabled": "false"})
    assert settings.load()["backup_enabled"] is False
