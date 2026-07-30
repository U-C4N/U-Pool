from __future__ import annotations

import sys
import zipfile

import pytest

from upool import paths, release, updater
from upool.models import UPoolError


def make_bundle_zip(target, *, wrap: str | None = None, exe_bytes: int = 2 * 1024 * 1024) -> None:
    """A zip that looks like ``scripts/build.py`` output."""
    prefix = f"{wrap}/" if wrap else ""
    with zipfile.ZipFile(target, "w") as zf:
        zf.writestr(f"{prefix}{updater.EXE_NAME}", b"M" * exe_bytes)
        zf.writestr(f"{prefix}{updater.INTERNAL_DIR}/base_library.zip", b"lib")


# ------------------------------------------------------------------- the gate


def test_can_self_update_refuses_a_source_checkout(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(sys, "frozen", raising=False)
    ok, blocker = updater.can_self_update()
    assert ok is False
    assert "runs from source" in blocker


def test_can_self_update_refuses_outside_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    ok, blocker = updater.can_self_update()
    assert ok is False
    assert blocker == updater.NOT_WINDOWS_BLOCKER


def test_can_self_update_refuses_something_that_is_not_a_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    ok, blocker = updater.can_self_update()
    assert ok is False
    assert blocker == updater.NOT_A_BUNDLE_BLOCKER


def test_can_self_update_explains_an_unwritable_parent(monkeypatch, tmp_path):
    root = tmp_path / "Program Files" / "U-Pool"
    (root / updater.INTERNAL_DIR).mkdir(parents=True)
    (root / updater.EXE_NAME).write_bytes(b"exe")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(root / updater.EXE_NAME))

    def deny(*_args, **_kwargs):
        raise OSError("access is denied")

    monkeypatch.setattr(updater.tempfile, "mkdtemp", deny)
    ok, blocker = updater.can_self_update()
    assert ok is False
    assert "administrator rights" in blocker
    assert str(root.parent) in blocker


def test_can_self_update_accepts_a_bundle_in_a_writable_folder(monkeypatch, tmp_path):
    root = tmp_path / "apps" / "U-Pool"
    (root / updater.INTERNAL_DIR).mkdir(parents=True)
    (root / updater.EXE_NAME).write_bytes(b"exe")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(root / updater.EXE_NAME))
    assert updater.can_self_update() == (True, "")


# ------------------------------------------------------------------- download


def _release(**kwargs) -> release.Release:
    defaults = {
        "tag": "v0.6.0",
        "version": "0.6.0",
        "notes": "",
        "html_url": "https://github.com/U-C4N/U-Pool/releases/latest",
        "published_at": "",
        "asset_name": "U-Pool-0.6.0-win64.zip",
        "asset_url": "https://github.com/U-C4N/U-Pool/releases/download/v0.6.0/U-Pool-0.6.0-win64.zip",
        "asset_size": 4,
        "asset_sha256": "",
    }
    defaults.update(kwargs)
    return release.Release(**defaults)


def test_download_refuses_a_release_without_an_asset():
    with pytest.raises(UPoolError, match="no Windows download"):
        updater.download(_release(asset_url="", asset_name=""))


def test_download_refuses_a_url_that_is_not_github():
    with pytest.raises(UPoolError, match="not on GitHub"):
        updater.download(_release(asset_url="https://evil.test/U-Pool.zip"))


def test_a_complete_cached_download_skips_the_network(monkeypatch):
    rel = _release()
    cache = paths.update_cache_dir()
    cache.mkdir(parents=True)
    archive = cache / rel.asset_name
    archive.write_bytes(b"data")
    updater.atomicio.write_json(
        updater._sidecar(archive),
        {"url": rel.asset_url, "size": 4, "sha256": "x", "complete": True},
    )

    def explode(*_args, **_kwargs):
        raise AssertionError("the network must not be touched for a cached download")

    monkeypatch.setattr(updater, "_open_download", explode)
    assert updater.download(rel) == archive


def test_a_half_written_download_is_not_treated_as_cached(monkeypatch):
    rel = _release()
    cache = paths.update_cache_dir()
    cache.mkdir(parents=True)
    archive = cache / rel.asset_name
    archive.write_bytes(b"da")
    updater.atomicio.write_json(
        updater._sidecar(archive), {"size": 4, "sha256": "x", "complete": True}
    )
    assert updater._cached(rel) is None


def test_a_cached_download_of_the_wrong_size_is_not_reused():
    rel = _release(asset_size=99)
    cache = paths.update_cache_dir()
    cache.mkdir(parents=True)
    archive = cache / rel.asset_name
    archive.write_bytes(b"data")
    updater.atomicio.write_json(
        updater._sidecar(archive), {"size": 4, "sha256": "x", "complete": True}
    )
    assert updater._cached(rel) is None


# -------------------------------------------------------------------- extract


def test_extract_unpacks_and_lifts_a_wrapping_folder(tmp_path):
    archive = tmp_path / "bundle.zip"
    make_bundle_zip(archive, wrap="U-Pool")
    target = tmp_path / "staged"
    updater.extract(archive, target)
    assert (target / updater.EXE_NAME).exists()
    assert (target / updater.INTERNAL_DIR / "base_library.zip").exists()
    assert not (target / "U-Pool").exists()


def test_extract_works_without_a_wrapping_folder(tmp_path):
    archive = tmp_path / "bundle.zip"
    make_bundle_zip(archive)
    target = tmp_path / "staged"
    updater.extract(archive, target)
    assert (target / updater.EXE_NAME).exists()


@pytest.mark.parametrize("evil", ["../evil.exe", "a/../../evil.exe", "C:/evil.exe", "/evil.exe"])
def test_extract_rejects_a_traversal_path(tmp_path, evil):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(evil, b"pwned")
    with pytest.raises(UPoolError, match="unsafe path"):
        updater.extract(archive, tmp_path / "staged")
    assert not (tmp_path / "evil.exe").exists()


def test_extract_rejects_a_bundle_without_a_real_exe(tmp_path):
    archive = tmp_path / "tiny.zip"
    make_bundle_zip(archive, exe_bytes=10)
    with pytest.raises(UPoolError, match="usable"):
        updater.extract(archive, tmp_path / "staged")


def test_extract_rejects_a_bundle_missing_its_internal_folder(tmp_path):
    archive = tmp_path / "bare.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(updater.EXE_NAME, b"M" * (2 * 1024 * 1024))
    with pytest.raises(UPoolError, match=updater.INTERNAL_DIR):
        updater.extract(archive, tmp_path / "staged")


def test_extract_rejects_something_that_is_not_a_zip(tmp_path):
    archive = tmp_path / "nope.zip"
    archive.write_bytes(b"this is not a zip")
    with pytest.raises(UPoolError, match="readable zip"):
        updater.extract(archive, tmp_path / "staged")


# ----------------------------------------------------------------------- swap


def test_the_swap_script_moves_out_moves_in_and_rolls_back(tmp_path):
    script = updater.write_swap_script(
        tmp_path / "U-Pool", tmp_path / ".new", tmp_path / ".old", "0.6.0"
    )
    text = script.read_text(encoding="utf-8")
    assert f'move "%CUR%" "%OLD%"' in text
    assert f'move "%NEW%" "%CUR%"' in text
    # The rollback, and the fact it is only reached when the second move failed.
    assert 'move "%OLD%" "%CUR%"' in text
    assert "rolled_back" in text
    # It must wait for us rather than racing the exit, and it must relaunch.
    assert "tasklist.exe" in text
    assert str(updater.os.getpid()) in text
    assert f'start "" "%CUR%\\{updater.EXE_NAME}"' in text
    # ``timeout /t`` needs a console this detached script does not have.
    assert "timeout /t" not in text
    assert "ping.exe" in text


def test_reconcile_reports_a_successful_swap_and_clears_the_marker():
    marker = paths.update_dir() / updater.SWAP_RESULT
    marker.parent.mkdir(parents=True)
    marker.write_text("ok 0.5.0\n", encoding="utf-8")
    assert updater.reconcile() == {"installed_from": "0.5.0"}
    assert not marker.exists()
    # Read once: a second launch must not repeat the toast.
    assert updater.reconcile() == {}


def test_reconcile_reports_a_rollback():
    marker = paths.update_dir() / updater.SWAP_RESULT
    marker.parent.mkdir(parents=True)
    marker.write_text("rolled_back\n", encoding="utf-8")
    assert updater.reconcile() == {"install_failed": "rolled_back"}


def test_reconcile_clears_stale_siblings_and_scripts(monkeypatch, tmp_path):
    root = tmp_path / "apps" / "U-Pool"
    (root / updater.INTERNAL_DIR).mkdir(parents=True)
    (root / updater.EXE_NAME).write_bytes(b"exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(root / updater.EXE_NAME))
    stale_old = root.parent / f"{updater.OLD_PREFIX}0.4.0"
    stale_new = root.parent / f"{updater.NEW_PREFIX}0.6.0"
    for folder in (stale_old, stale_new):
        folder.mkdir()
        (folder / "junk.txt").write_text("junk", encoding="utf-8")
    script = paths.update_dir() / "swap-0.6.0.cmd"
    script.parent.mkdir(parents=True)
    script.write_text("@echo off", encoding="utf-8")

    updater.reconcile()
    assert not stale_old.exists()
    assert not stale_new.exists()
    assert not script.exists()
    assert root.exists()


# -------------------------------------------------------------------- updater


def test_a_failed_check_keeps_what_we_last_knew(monkeypatch):
    def refuse(timeout=None):
        raise UPoolError("Could not reach GitHub: offline")

    monkeypatch.setattr(release, "fetch_latest", refuse)
    up = updater.Updater()
    up._set(release=_release(version="9.9.9").to_dict())
    up._check()
    snapshot = up.snapshot()
    assert snapshot["phase"] == updater.PHASE_AVAILABLE
    assert "offline" in snapshot["error"]
    assert snapshot["release"]["version"] == "9.9.9"


def test_a_check_that_finds_nothing_newer_says_so(monkeypatch):
    monkeypatch.setattr(release, "fetch_latest", lambda timeout=None: _release(version="0.0.1"))
    up = updater.Updater()
    up._check()
    snapshot = up.snapshot()
    assert snapshot["phase"] == updater.PHASE_UP_TO_DATE
    assert snapshot["busy"] is False


def test_a_check_is_throttled_unless_forced(monkeypatch):
    calls = []
    monkeypatch.setattr(release, "fetch_latest", lambda timeout=None: calls.append(1) or _release())
    updater.settings.update({"update_check_enabled": True})
    up = updater.Updater()
    up.check_async(force=True)
    if up._worker.handle:
        up._worker.handle.join(timeout=5)
    assert len(calls) == 1
    # The timestamp the first check stored is what stops the second one.
    assert updater.settings.load()["update_last_check"] > 0
    up.check_async()
    if up._worker.handle:
        up._worker.handle.join(timeout=5)
    assert len(calls) == 1


def test_a_check_does_nothing_while_the_switch_is_off(monkeypatch):
    calls = []
    monkeypatch.setattr(release, "fetch_latest", lambda timeout=None: calls.append(1) or _release())
    updater.settings.update({"update_check_enabled": False, "update_last_check": 0})
    up = updater.Updater()
    up.check_async()
    if up._worker.handle:
        up._worker.handle.join(timeout=5)
    assert calls == []


def test_install_refuses_with_the_blocker_text(monkeypatch):
    monkeypatch.setattr(updater, "can_self_update", lambda: (False, "nope, running from source"))
    up = updater.Updater()
    with pytest.raises(UPoolError, match="running from source"):
        up.install_async()


def test_install_refuses_when_there_is_nothing_newer(monkeypatch):
    monkeypatch.setattr(updater, "can_self_update", lambda: (True, ""))
    up = updater.Updater()
    up._set(release=_release(version="0.0.1").to_dict())
    with pytest.raises(UPoolError, match="already the latest"):
        up.install_async()


def test_skipping_a_version_is_remembered(monkeypatch):
    monkeypatch.setattr(release, "fetch_latest", lambda timeout=None: _release(version="9.9.9"))
    up = updater.Updater()
    up.skip("9.9.9")
    assert up.snapshot()["skipped_version"] == "9.9.9"
    # A fresh instance reads it back out of settings.json.
    assert updater.Updater().snapshot()["skipped_version"] == "9.9.9"
