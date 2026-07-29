from __future__ import annotations

import sys

import pytest

from upool import autostart
from upool.models import UPoolError

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="the Run key only exists on Windows"
)

# The 12-byte blob Task Manager writes: first byte 0x02 approved, 0x03 switched off.
APPROVED_BLOB = bytes([0x02]) + bytes(11)
VETOED_BLOB = bytes([0x03]) + bytes(11)


def test_unsupported_platform_says_so_instead_of_failing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    entry = autostart.state()
    assert entry == {
        "supported": False,
        "enabled": False,
        "blocked": False,
        "command": "",
        "detail": autostart.UNSUPPORTED_NOTE,
    }
    with pytest.raises(UPoolError, match="Windows"):
        autostart.apply(True)
    assert autostart.reconcile(True) is False


def test_a_bundle_path_with_spaces_is_quoted(monkeypatch, tmp_path):
    # Explorer splits the Run value on the first space, so "C:\Program Files\..."
    # unquoted would send Windows looking for C:\Program.exe.
    bundle = tmp_path / "U Pool" / "U-Pool.exe"
    bundle.parent.mkdir()
    bundle.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle))
    assert autostart.command() == f'"{bundle}"'


def test_a_source_run_registers_the_module_entry_point(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert autostart.command().endswith("-m upool")


@windows_only
def test_toggle_round_trip():
    assert autostart.is_enabled() is False
    autostart.apply(True)
    assert autostart.is_enabled() is True
    assert autostart.state()["command"] == autostart.command()
    autostart.apply(False)
    assert autostart.is_enabled() is False


@windows_only
def test_switching_off_twice_is_not_an_error():
    autostart.apply(False)
    autostart.apply(False)
    assert autostart.is_enabled() is False


@windows_only
def test_reconcile_repoints_an_entry_left_by_an_older_install():
    autostart.apply(True)
    autostart._write_run_value('"C:\\gone\\U-Pool\\U-Pool.exe"')
    assert autostart.reconcile(True) is True
    assert autostart._read_run_value() == autostart.command()
    # Second pass has nothing left to do.
    assert autostart.reconcile(True) is False


@windows_only
def test_reconcile_never_adds_an_entry_the_user_did_not_ask_for():
    assert autostart.reconcile(False) is False
    assert autostart._read_run_value() == ""


@windows_only
def test_reconcile_leaves_an_entry_deleted_outside_u_pool_deleted():
    # regedit, msconfig or a cleanup tool removed it: that is a decision, not drift.
    assert autostart.reconcile(True) is False
    assert autostart._read_run_value() == ""


def _write_approval(blob: bytes) -> None:
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, autostart.APPROVED_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, autostart.VALUE_NAME, 0, winreg.REG_BINARY, blob)


@windows_only
def test_an_entry_disabled_in_task_manager_is_reported_but_still_removable():
    autostart.apply(True)
    _write_approval(VETOED_BLOB)

    entry = autostart.state()
    # Our entry is still registered - Windows is just ignoring it. Reporting that as
    # "off" would leave the switch with no gesture that removes the entry.
    assert entry["enabled"] is True
    assert entry["blocked"] is True
    assert "Task Manager" in entry["detail"]

    # Ticking our own switch again must not quietly overrule Windows.
    autostart.apply(True)
    assert autostart.state()["blocked"] is True

    # Switching it off here still works, veto or not.
    autostart.apply(False)
    assert autostart.state()["enabled"] is False


@windows_only
def test_an_approved_entry_is_not_mistaken_for_a_veto():
    autostart.apply(True)
    for blob in (APPROVED_BLOB, bytes([0x06]) + bytes(11), bytes(12)):
        _write_approval(blob)
        assert autostart.state()["blocked"] is False, blob.hex()
