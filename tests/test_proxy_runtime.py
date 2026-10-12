from __future__ import annotations

import hashlib
import io
import json
import zipfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from upool import paths
from upool.proxy.models import ProxyError, UpstreamResponse
from upool.proxy.runtime import ManagedEngines, RuntimeInstaller, extract_zip


def archive(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as package:
        for name, content in entries.items():
            package.writestr(name, content)
    return path


def installer(tmp_path: Path, bundle: Path, digest: str | None = None) -> RuntimeInstaller:
    component = {"id": "engine", "version": "1", "bundle": bundle.name,
                 "sha256": digest or hashlib.sha256(bundle.read_bytes()).hexdigest(), "executable": "engine.exe"}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"components": [component], "worker": {**component, "id": "chatgpt-worker"}}))
    return RuntimeInstaller(manifest)


def test_extract_rejects_traversal_and_symlink(tmp_path):
    target = tmp_path / "install"
    target.mkdir()
    malicious = archive(tmp_path, {"../outside": b"bad"})
    with pytest.raises(ProxyError):
        extract_zip(malicious, target)
    assert not (tmp_path / "outside").exists()
    with zipfile.ZipFile(malicious, "w") as package:
        entry = zipfile.ZipInfo("link")
        entry.external_attr = (0o120777 << 16)
        package.writestr(entry, "outside")
    with pytest.raises(ProxyError):
        extract_zip(malicious, target)


def test_install_pins_hash_and_preserves_previous(tmp_path):
    bundle = archive(tmp_path, {"engine.exe": b"first"})
    subject = installer(tmp_path, bundle)
    component = subject.components()[0]
    subject._install_component(component)
    assert subject.installed(component)
    installed = subject.directory(component)
    bundle = archive(tmp_path, {"engine.exe": b"tampered"})
    with pytest.raises(ValueError, match="checksum"):
        subject._install_component(component)
    assert (installed / "engine.exe").read_bytes() == b"first"


def test_snapshot_is_passive_and_missing_build_is_honest(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("snapshot reached the network"))
    subject = ManagedEngines()
    snapshot = subject.snapshot()
    assert {entry["id"] for entry in snapshot} == {"chrome", "cliproxyapi", "chatgpt-worker"}
    assert all(not entry["installed"] for entry in snapshot)
    assert paths.runtimes_dir().is_relative_to(paths.app_home())


def test_account_directory_and_adoption_only_managed_paths(tmp_path):
    subject = ManagedEngines()
    with pytest.raises(ProxyError):
        subject.account_dir("../unsafe")
    source = subject.account_dir("new")
    source.mkdir(parents=True)
    (source / "auth.json").write_text("private fake token")
    target = subject.account_dir("existing")
    target.mkdir()
    (target / "old").write_text("old")
    subject.adopt_account("new", "existing")
    assert not source.exists()
    assert (target / "auth.json").exists()
    assert not (target / "old").exists()
    subject.delete_account_data("existing")
    assert not target.exists()


def replacement_profiles(subject):
    source, target = subject.account_dir("new"), subject.account_dir("existing")
    source.mkdir(parents=True)
    target.mkdir()
    (source / "session.txt").write_text("new fake session")
    (target / "session.txt").write_text("old fake session")
    return source, target


def denied_profile_path(path):
    error = PermissionError(13, "Fake managed directory is locked", str(path))
    error.winerror = 5
    return error


def test_account_adoption_preserves_both_profiles_when_old_directory_is_locked(monkeypatch):
    subject = ManagedEngines()
    source, target = replacement_profiles(subject)
    original_rename = Path.rename
    def rename(path, destination):
        if path == target:
            raise denied_profile_path(path)
        return original_rename(path, destination)
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr("upool.proxy.runtime._MANAGED_FS_RETRY_SECONDS", 0)
    monkeypatch.setattr("shutil.rmtree", lambda *args, **kwargs: pytest.fail("A failed adoption deleted a profile"))
    with pytest.raises(ProxyError) as error:
        subject.adopt_account("new", "existing")
    assert error.value.code == "account_replace_failed"
    assert str(target) not in str(error.value)
    assert (target / "session.txt").read_text() == "old fake session"
    assert (source / "session.txt").read_text() == "new fake session"


def test_account_adoption_restores_old_profile_when_new_directory_cannot_commit(monkeypatch):
    subject = ManagedEngines()
    source, target = replacement_profiles(subject)
    original_rename = Path.rename
    def rename(path, destination):
        if path == source:
            raise denied_profile_path(path)
        return original_rename(path, destination)
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr("upool.proxy.runtime._MANAGED_FS_RETRY_SECONDS", 0)
    with pytest.raises(ProxyError) as error:
        subject.adopt_account("new", "existing")
    assert error.value.code == "account_replace_failed"
    assert (target / "session.txt").read_text() == "old fake session"
    assert (source / "session.txt").read_text() == "new fake session"
    assert not list(target.parent.glob(".existing.previous-*"))


def test_failed_rollback_still_preserves_previous_profile_data(monkeypatch):
    subject = ManagedEngines()
    source, target = replacement_profiles(subject)
    original_rename = Path.rename
    def rename(path, destination):
        if path == source or path.name.startswith(".existing.previous-"):
            raise denied_profile_path(path)
        return original_rename(path, destination)
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr("upool.proxy.runtime._MANAGED_FS_RETRY_SECONDS", 0)
    with pytest.raises(ProxyError) as error:
        subject.adopt_account("new", "existing")
    assert error.value.code == "account_restore_failed"
    backups = list(target.parent.glob(".existing.previous-*"))
    assert len(backups) == 1
    assert (backups[0] / "session.txt").read_text() == "old fake session"
    assert (source / "session.txt").read_text() == "new fake session"


def test_successful_adoption_is_not_reverted_when_previous_profile_cleanup_is_locked(monkeypatch):
    subject = ManagedEngines()
    source, target = replacement_profiles(subject)
    original_remove = __import__("shutil").rmtree
    def remove(path, *args, **kwargs):
        if Path(path).name.startswith(".existing.previous-"):
            raise denied_profile_path(path)
        return original_remove(path, *args, **kwargs)
    monkeypatch.setattr("shutil.rmtree", remove)
    monkeypatch.setattr("upool.proxy.runtime._MANAGED_FS_RETRY_SECONDS", 0)
    subject.adopt_account("new", "existing")
    assert not source.exists()
    assert (target / "session.txt").read_text() == "new fake session"
    backups = list(target.parent.glob(".existing.previous-*"))
    assert len(backups) == 1
    assert backups[0].resolve().is_relative_to(paths.proxy_dir().resolve())
    assert (backups[0] / "session.txt").read_text() == "old fake session"


def test_account_adoption_retries_actual_windows_access_violation_before_commit(monkeypatch):
    subject = ManagedEngines()
    source, target = replacement_profiles(subject)
    original_rename = Path.rename
    attempts = []
    def rename(path, destination):
        if path == target:
            attempts.append(path)
            if len(attempts) == 1:
                raise denied_profile_path(path)
        return original_rename(path, destination)
    monkeypatch.setattr(Path, "rename", rename)
    with monkeypatch.context() as windows:
        windows.setattr("upool.proxy.runtime.sys.platform", "win32")
        windows.setattr("upool.proxy.runtime._MANAGED_FS_RETRY_SECONDS", 0.5)
        subject.adopt_account("new", "existing")
    assert len(attempts) == 2
    assert not source.exists()
    assert (target / "session.txt").read_text() == "new fake session"


def test_missing_verified_profile_does_not_replace_existing_account():
    subject = ManagedEngines()
    target = subject.account_dir("existing")
    target.mkdir(parents=True)
    (target / "session.txt").write_text("old fake session")
    with pytest.raises(ProxyError) as error:
        subject.adopt_account("missing", "existing")
    assert error.value.code == "account_replace_failed"
    assert (target / "session.txt").read_text() == "old fake session"


def test_account_deletion_purges_profile_and_its_exact_generated_backups_only():
    subject = ManagedEngines()
    source, target = replacement_profiles(subject)
    generated = target.parent / (".existing.previous-" + "a" * 32)
    generated.mkdir()
    (generated / "session.txt").write_text("old fake session")
    retained = [target.parent / name for name in (
        ".other.previous-" + "b" * 32,
        ".existing2.previous-" + "c" * 32,
        ".existing.previous-invalid",
        ".existing.previous-" + "a" * 31,
        ".existing.previous-" + "E" * 32,
    )]
    for directory in retained:
        directory.mkdir()
        (directory / "session.txt").write_text("other fake session")
    named_file = target.parent / (".existing.previous-" + "d" * 32)
    named_file.write_text("not a generated profile directory")
    subject.delete_account_data("existing")
    assert not target.exists() and not generated.exists()
    assert (source / "session.txt").read_text() == "new fake session"
    assert all((directory / "session.txt").read_text() == "other fake session" for directory in retained)
    assert named_file.is_file()


def test_locked_backup_keeps_live_profile_and_returns_sanitized_retryable_delete_error(monkeypatch):
    subject = ManagedEngines()
    _, target = replacement_profiles(subject)
    backup = target.parent / (".existing.previous-" + "a" * 32)
    backup.mkdir()
    (backup / "session.txt").write_text("old fake session")
    original_remove = __import__("shutil").rmtree
    def remove(path, *args, **kwargs):
        if Path(path) == backup:
            raise denied_profile_path(path)
        return original_remove(path, *args, **kwargs)
    monkeypatch.setattr("shutil.rmtree", remove)
    monkeypatch.setattr("upool.proxy.runtime._MANAGED_FS_RETRY_SECONDS", 0)
    with pytest.raises(ProxyError) as error:
        subject.delete_account_data("existing")
    assert error.value.status == 503 and error.value.code == "account_delete_failed"
    assert str(backup) not in str(error.value)
    assert (backup / "session.txt").read_text() == "old fake session"
    assert (target / "session.txt").read_text() == "old fake session"
    monkeypatch.setattr("shutil.rmtree", original_remove)
    subject.delete_account_data("existing")
    assert not target.exists() and not backup.exists()


def test_deletion_purges_backup_retained_after_successful_adoption(monkeypatch):
    subject = ManagedEngines()
    source, target = replacement_profiles(subject)
    original_remove = __import__("shutil").rmtree
    def remove(path, *args, **kwargs):
        if Path(path).name.startswith(".existing.previous-"):
            raise denied_profile_path(path)
        return original_remove(path, *args, **kwargs)
    monkeypatch.setattr("shutil.rmtree", remove)
    monkeypatch.setattr("upool.proxy.runtime._MANAGED_FS_RETRY_SECONDS", 0)
    subject.adopt_account("new", "existing")
    assert not source.exists()
    assert list(target.parent.glob(".existing.previous-*"))
    monkeypatch.setattr("shutil.rmtree", original_remove)
    subject.delete_account_data("existing")
    assert not target.exists()
    assert not list(target.parent.glob(".existing.previous-*"))


def test_refresh_uses_current_profile_without_reimport(monkeypatch):
    from types import SimpleNamespace
    subject = ManagedEngines()
    instance = SimpleNamespace(active=0, touched=0)
    monkeypatch.setattr(subject, "_start", lambda account: instance)
    requests = []
    monkeypatch.setattr(subject, "_json", lambda current, endpoint, payload, *args:
                        requests.append((current, endpoint, payload)) or {"email": "fake@example.invalid"})
    subject.refresh_chatgpt({"id": "fake", "cookies": [{"name": "stale", "value": "fake"}]})
    assert requests == [(instance, "/internal/facts", {})]
    assert instance.active == 0


def test_restart_preserves_valid_manual_profile_without_overwriting_its_cookies(monkeypatch):
    from types import SimpleNamespace
    subject = ManagedEngines()
    instance = SimpleNamespace()
    calls = []
    def request(current, endpoint, payload):
        assert current is instance
        calls.append((endpoint, payload))
        return {"user_id": "manual-login", "models": ["auto"]}
    monkeypatch.setattr(subject, "_json", request)
    subject._restore_chatgpt_session(instance, {"provider": "chatgpt", "cookies": [{"value": "old fake"}]})
    assert calls == [("/internal/facts", {})]


@pytest.mark.parametrize("provider,cookies", [("grok", [{"value": "fake"}]), ("chatgpt", []), ("chatgpt", None)])
def test_restart_does_not_probe_other_provider_or_accounts_without_imported_cookies(monkeypatch, provider, cookies):
    subject = ManagedEngines()
    monkeypatch.setattr(subject, "_json", lambda *args, **kwargs: pytest.fail("Unnecessary session probe"))
    subject._restore_chatgpt_session(object(), {"provider": provider, "cookies": cookies})


@pytest.mark.parametrize("status", [400, 403, 429, 502, 503])
def test_restart_never_replaces_cookies_after_non_authentication_probe_failure(monkeypatch, status):
    subject = ManagedEngines()
    calls = []
    def request(instance, endpoint, payload):
        calls.append(endpoint)
        raise ProxyError("Fake upstream failure", status, "fake")
    monkeypatch.setattr(subject, "_json", request)
    subject._restore_chatgpt_session(object(), {"provider": "chatgpt", "cookies": [{"value": "old fake"}]})
    assert calls == ["/internal/facts"]


def test_restart_restores_saved_cookies_once_after_exact_authentication_failure(monkeypatch):
    subject = ManagedEngines()
    cookies = [{"name": "session", "value": "fake imported"}]
    calls = []
    def request(instance, endpoint, payload):
        calls.append((endpoint, payload))
        if endpoint == "/internal/facts":
            raise ProxyError("Fake expired browser session", 401, "auth_expired")
        return {"user_id": "restored", "models": ["auto"]}
    monkeypatch.setattr(subject, "_json", request)
    subject._restore_chatgpt_session(object(), {"provider": "chatgpt", "cookies": cookies})
    assert calls == [("/internal/facts", {}), ("/internal/verify", {"cookies": cookies})]


def test_expired_saved_cookies_leave_profile_usable_for_manual_login(monkeypatch):
    from types import SimpleNamespace
    subject = ManagedEngines()
    instance = SimpleNamespace(active=0, touched=0)
    calls = []
    def request(current, endpoint, payload, *args):
        calls.append(endpoint)
        if endpoint == "/internal/reveal":
            return {"status": "ok"}
        raise ProxyError("Fake expired browser session", 401, "auth_expired")
    monkeypatch.setattr(subject, "_json", request)
    monkeypatch.setattr(subject, "_start", lambda account: instance)
    account = {"id": "existing", "provider": "chatgpt", "cookies": [{"value": "expired fake"}]}
    subject._restore_chatgpt_session(instance, account)
    assert subject.open_profile(account) == {"status": "ok"}
    with pytest.raises(ProxyError) as error:
        subject.refresh_chatgpt(account)
    assert error.value.status == 401
    assert calls == ["/internal/facts", "/internal/verify", "/internal/reveal", "/internal/facts"]
    assert instance.active == 0


def test_new_runtime_restores_session_after_http_ready_and_not_on_healthy_reuse(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    subject = ManagedEngines()
    cookies = [{"value": "fake imported"}]
    calls = []
    subject.installer = SimpleNamespace(executable=lambda component: tmp_path / (component + ".exe"))
    def owned_process(*args):
        return SimpleNamespace(process=SimpleNamespace(poll=lambda: None), close=lambda: None)
    monkeypatch.setattr("upool.proxy.runtime.OwnedProcess", owned_process)
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: nullcontext())
    def request(instance, endpoint, payload=None, *args):
        calls.append(endpoint)
        if endpoint == "/internal/facts":
            raise ProxyError("Fake expired browser session", 401, "auth_expired")
        return {"status": "ready"}
    monkeypatch.setattr(subject, "_json", request)
    account = {"id": "restart", "provider": "chatgpt", "cookies": cookies}
    instance = subject._start(account)
    assert subject._start(account) is instance
    assert calls == ["/internal/facts", "/internal/verify", "/health"]
    assert subject._instances["restart"] is instance


def test_complete_normalizes_endpoint_and_releases_busy_lease(monkeypatch):
    from types import SimpleNamespace
    subject = ManagedEngines()
    instance = SimpleNamespace(active=0, touched=0)
    monkeypatch.setattr(subject, "_start", lambda account: instance)
    requested = []
    monkeypatch.setattr(subject, "_request", lambda instance, endpoint, payload:
                        requested.append(endpoint) or UpstreamResponse(200, {}, b"response"))
    response = subject.complete({"id": "fake"}, "chat/completions", {})
    assert requested == ["/v1/chat/completions"]
    assert instance.active == 1
    response.close()
    response.close()
    assert instance.active == 0


def test_cancellation_interrupts_wait_for_initial_headers():
    from types import SimpleNamespace
    started, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            started.set()
            release.wait(timeout=3)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    subject = ManagedEngines()
    instance = SimpleNamespace(port=server.server_port, key="fake-key", management_key="fake-management")
    errors = []
    def request():
        try:
            subject._request(instance, "/v1/chat/completions", {}, cancelled=cancelled)
        except ProxyError as error:
            errors.append(error)
    thread = threading.Thread(target=request, daemon=True)
    thread.start()
    try:
        assert started.wait(timeout=2)
        cancelled.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert errors[0].status == 499
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)


def test_metadata_operations_hold_browser_lease(monkeypatch):
    from types import SimpleNamespace
    subject = ManagedEngines()
    instance = SimpleNamespace(active=0, touched=0)
    monkeypatch.setattr(subject, "_start", lambda account: instance)
    def action(current, endpoint, payload, *args):
        assert current.active == 1
        raise ProxyError("fake failure")
    monkeypatch.setattr(subject, "_json", action)
    with pytest.raises(ProxyError):
        subject.verify_chatgpt({"id": "fake"}, [])
    assert instance.active == 0
