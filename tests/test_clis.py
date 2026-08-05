from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from upool import clis
from upool.api import Api

# The sandbox fixture replaces `clis.resolve` so nothing in the suite can run the
# developer's own claude.cmd. The two tests that are *about* resolution put the
# real one back, which needs a reference taken before the patch lands.
real_resolve = clis.resolve


@pytest.fixture(autouse=True)
def cold_cache():
    """The sandbox fixture stubs `resolve` to find nothing; these tests own it."""
    clis.clear_cache()
    yield
    clis.join_worker(timeout=5)
    clis.clear_cache()


def fake_run(stdout: str = "", stderr: str = "", exc: Exception | None = None, seen: dict | None = None):
    def run(cmd, **kwargs):
        assert kwargs["capture_output"] is True
        assert cmd[1] == clis.VERSION_ARG
        if seen is not None:
            seen.update(kwargs)
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(cmd, 0, stdout, stderr)

    return run


def test_a_version_is_read_out_of_stdout(monkeypatch):
    monkeypatch.setattr(clis, "resolve", lambda name: f"/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake_run(stdout="2.1.4 (Claude Code)\n"))
    result = clis.probe("claude")
    assert result == {
        "id": "claude",
        "label": "Claude Code",
        "version": "2.1.4",
        "path": "/bin/claude",
        "found": True,
        "error": "",
    }


def test_a_version_printed_to_stderr_still_counts(monkeypatch):
    monkeypatch.setattr(clis, "resolve", lambda name: f"/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake_run(stderr="codex-cli 0.48.0\n"))
    assert clis.probe("codex")["version"] == "0.48.0"


def test_a_prerelease_suffix_is_kept(monkeypatch):
    monkeypatch.setattr(clis, "resolve", lambda name: f"/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake_run(stdout="v1.2.3-beta.4"))
    assert clis.probe("claude")["version"] == "1.2.3-beta.4"


def test_not_installed_is_reported_as_such(monkeypatch):
    monkeypatch.setattr(clis, "resolve", lambda name: "")
    result = clis.probe("claude")
    assert result["found"] is False
    assert result["version"] == ""
    assert "Not installed" in result["error"]


def test_installed_but_broken_is_a_different_answer_from_missing(monkeypatch):
    """Found on disk and unable to answer is a PATH or shim problem, not absence.

    Reporting it as "not installed" sends the user to reinstall something that is
    already there, so the two cases are kept apart.
    """
    monkeypatch.setattr(clis, "resolve", lambda name: "/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run(stderr="node: bad option --version\n"))
    result = clis.probe("claude")
    assert result["found"] is True
    assert result["version"] == ""
    assert result["error"] == "node: bad option --version"


def test_a_hung_probe_gives_up_rather_than_holding_the_button(monkeypatch):
    monkeypatch.setattr(clis, "resolve", lambda name: "/bin/claude")
    monkeypatch.setattr(
        subprocess, "run", fake_run(exc=subprocess.TimeoutExpired("claude", clis.TIMEOUT))
    )
    assert "did not answer" in clis.probe("claude")["error"]


def test_an_executable_that_will_not_start_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(clis, "resolve", lambda name: "/bin/claude")
    monkeypatch.setattr(subprocess, "run", fake_run(exc=OSError(8, "Exec format error")))
    assert "could not be started" in clis.probe("claude")["error"]


def test_resolve_falls_back_to_the_npm_directory(monkeypatch, tmp_path):
    """``which`` misses what a windowed process inherited no PATH for."""
    npm = tmp_path / "npm"
    npm.mkdir()
    suffix = ".cmd" if sys.platform == "win32" else ""
    shim = npm / f"claude{suffix}"
    shim.write_text("", encoding="utf-8")
    monkeypatch.setattr(clis, "resolve", real_resolve)
    monkeypatch.setattr(clis.shutil, "which", lambda name: None)
    monkeypatch.setattr(clis, "_search_roots", lambda: [npm])
    assert clis.resolve("claude") == str(shim)


def test_resolve_prefers_what_which_found(monkeypatch):
    monkeypatch.setattr(clis, "resolve", real_resolve)
    monkeypatch.setattr(clis.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(clis, "_search_roots", lambda: pytest.fail("should not be reached"))
    assert clis.resolve("codex") == "/usr/bin/codex"


def test_node_is_put_back_on_the_path_when_the_shim_would_miss_it(monkeypatch, tmp_path):
    """The npm shims are batch files that call `node`; resolving them is half the job.

    Confirmed against the real machine: `codex --version` came back
    `'"node"' is not recognized` from a process whose PATH had `%APPDATA%\\npm`
    but not the nodejs directory - the same inherited-PATH problem one layer down,
    and it reads like a broken install rather than a missing PATH entry.
    """
    nodejs = tmp_path / "nodejs"
    nodejs.mkdir()
    (nodejs / ("node.exe" if sys.platform == "win32" else "node")).write_text("", encoding="utf-8")
    seen: dict = {}
    monkeypatch.setattr(clis, "resolve", lambda name: f"/bin/{name}")
    monkeypatch.setattr(clis.shutil, "which", lambda name: None)
    monkeypatch.setattr(clis, "_node_roots", lambda: [nodejs])
    monkeypatch.setattr(subprocess, "run", fake_run(stdout="1.0.0", seen=seen))

    clis.probe("codex")
    assert seen["env"]["PATH"].startswith(str(nodejs))


def test_the_environment_is_left_alone_when_node_is_already_reachable(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(clis, "resolve", lambda name: f"/bin/{name}")
    monkeypatch.setattr(clis.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(subprocess, "run", fake_run(stdout="1.0.0", seen=seen))

    clis.probe("codex")
    # None, not a copy: there is nothing to add, so the child inherits as-is.
    assert seen["env"] is None


def test_probe_all_is_cached_until_forced(monkeypatch):
    calls = []

    def counted(name, label=""):
        calls.append(name)
        return {"id": name, "label": label, "version": "1.0.0", "path": "", "found": True, "error": ""}

    monkeypatch.setattr(clis, "probe", counted)
    clis.probe_all()
    clis.probe_all()
    assert calls == list(clis.PROBES)

    clis.probe_all(force=True)
    assert calls == list(clis.PROBES) * 2


def test_the_snapshot_names_every_tool_before_the_first_probe():
    snapshot = clis.snapshot()
    assert snapshot["ready"] is False
    assert [tool["id"] for tool in snapshot["tools"]] == list(clis.PROBES)
    assert all(tool["version"] == "" for tool in snapshot["tools"])


def test_bootstrap_carries_the_snapshot_without_waiting(monkeypatch):
    started = []

    def spy(force=True):
        started.append(force)
        return clis.snapshot()

    monkeypatch.setattr(clis, "refresh_async", spy)
    data = Api().bootstrap()["data"]
    assert [tool["label"] for tool in data["clis"]["tools"]] == ["Claude Code", "Codex"]
    # Kicked off in the background, and only when the cache is cold.
    assert started == [False]


def test_bootstrap_reports_the_probe_as_running(monkeypatch):
    """The payload has to say busy, or the UI never starts polling for the answer.

    Taking the snapshot before starting the thread reported `busy: false` on a
    probe that was about to begin, and the header sat on placeholders until
    someone pressed refresh.
    """
    ready = threading.Event()
    monkeypatch.setattr(clis, "probe", lambda name, label="": (
        ready.wait(5),
        {"id": name, "label": label, "version": "1.0.0", "path": "", "found": True, "error": ""},
    )[1])
    try:
        data = Api().bootstrap()["data"]
        assert data["clis"]["busy"] is True
        assert data["clis"]["ready"] is False
    finally:
        ready.set()


def test_a_probe_that_dies_still_leaves_the_cache_set(monkeypatch):
    """`ready` is what stops the UI polling, so a failure has to reach it too."""
    monkeypatch.setattr(clis, "probe_all", lambda force=False: (_ for _ in ()).throw(RuntimeError("boom")))
    clis.refresh_async(force=True)
    clis.join_worker(timeout=5)
    snapshot = clis.snapshot()
    assert snapshot["ready"] is True
    assert all("failed" in tool["error"] for tool in snapshot["tools"])


def test_the_refresh_endpoint_answers_immediately(monkeypatch):
    monkeypatch.setattr(clis, "probe", lambda name, label="": {
        "id": name, "label": label, "version": "9.9.9", "path": "", "found": True, "error": ""
    })
    api = Api()
    assert api.refresh_cli_versions()["ok"] is True
    # The worker is a daemon thread; joining it is what makes the assert stable.
    if clis._worker is not None:
        clis._worker.join(timeout=5)
    tools = api.cli_versions()["data"]["tools"]
    assert [tool["version"] for tool in tools] == ["9.9.9", "9.9.9"]


def test_a_second_unforced_refresh_over_a_warm_cache_does_not_deadlock(monkeypatch):
    """``refresh_async`` used to call ``snapshot`` while holding the same
    non-reentrant lock ``snapshot`` takes.

    Cold, the branch is skipped and nothing notices. Warm, the process stops dead
    - and the second call is not exotic: every ``Api.bootstrap`` after the first
    makes it, so reloading the webview hung the app with a blank window and no
    error anywhere. The assertion is that this test returns at all; it is run on a
    timer because a deadlocked pytest reports nothing.
    """
    monkeypatch.setattr(clis, "probe", lambda name, label="": {
        "id": name, "label": label, "version": "1.0.0", "path": "", "found": True, "error": ""
    })
    clis.refresh_async(force=True)
    clis.join_worker(timeout=5)
    assert clis.snapshot()["ready"] is True

    done = threading.Event()

    def second() -> None:
        clis.refresh_async(force=False)
        done.set()

    threading.Thread(target=second, daemon=True).start()
    assert done.wait(timeout=10), "refresh_async(force=False) deadlocked against snapshot()"
