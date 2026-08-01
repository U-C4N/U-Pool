from __future__ import annotations

import pytest

from upool import paths, sessions
from upool.api import Api
from upool.models import APP_CLAUDE, APP_CODEX, APP_HERMES, UPoolError


def seed_claude() -> None:
    root = paths.claude_dir()
    (root / "projects" / "repo-a").mkdir(parents=True)
    (root / "projects" / "repo-a" / "aaa.jsonl").write_text("x" * 100, encoding="utf-8")
    (root / "projects" / "repo-b").mkdir(parents=True)
    (root / "projects" / "repo-b" / "bbb.jsonl").write_text("y" * 50, encoding="utf-8")
    (root / "sessions").mkdir(parents=True)
    (root / "sessions" / "s.json").write_text("{}", encoding="utf-8")
    (root / "history.jsonl").write_text("z" * 10, encoding="utf-8")
    # Everything below is out of scope and must survive.
    (root / "settings.json").write_text('{"env": {}}', encoding="utf-8")
    (root / ".credentials.json").write_text('{"token": "keep"}', encoding="utf-8")
    (root / "file-history").mkdir()
    (root / "file-history" / "edit.diff").write_text("keep", encoding="utf-8")
    (root / "plugins").mkdir()
    (root / "plugins" / "p.json").write_text("keep", encoding="utf-8")


def seed_codex() -> None:
    root = paths.codex_dir()
    (root / "sessions" / "2026").mkdir(parents=True)
    (root / "sessions" / "2026" / "one.jsonl").write_text("a" * 20, encoding="utf-8")
    (root / "archived_sessions").mkdir(parents=True)
    (root / "archived_sessions" / "old.jsonl").write_text("b" * 30, encoding="utf-8")
    (root / "history.jsonl").write_text("c" * 5, encoding="utf-8")
    (root / "session_index.jsonl").write_text("d" * 5, encoding="utf-8")
    (root / "auth.json").write_text('{"OPENAI_API_KEY": "keep"}', encoding="utf-8")
    (root / "config.toml").write_text('model = "keep"', encoding="utf-8")
    (root / ".tmp").mkdir()
    (root / ".tmp" / "scratch").write_text("keep", encoding="utf-8")


def test_summary_counts_what_a_purge_would_take():
    seed_claude()
    report = sessions.summary(APP_CLAUDE)
    assert report["files"] == 4
    assert report["bytes"] == 100 + 50 + 2 + 10
    assert [entry["files"] for entry in report["entries"]] == [2, 1, 1]


def test_summary_reports_zero_for_a_machine_with_nothing_to_delete():
    report = sessions.summary(APP_CODEX)
    assert report["files"] == 0
    assert report["bytes"] == 0
    assert all(entry["exists"] is False for entry in report["entries"])


def test_purge_takes_the_transcripts_and_nothing_else():
    seed_claude()
    outcome = sessions.purge(APP_CLAUDE)

    root = paths.claude_dir()
    assert outcome["deleted"] == 4
    assert outcome["errors"] == []
    assert sessions.summary(APP_CLAUDE)["files"] == 0
    # The directories stay, because the CLI expects to find them.
    assert (root / "projects").is_dir()
    assert list((root / "projects").iterdir()) == []
    assert (root / "sessions").is_dir()
    assert not (root / "history.jsonl").exists()
    # And the things that are not transcripts are all still there.
    assert (root / "settings.json").read_text(encoding="utf-8") == '{"env": {}}'
    assert (root / ".credentials.json").exists()
    assert (root / "file-history" / "edit.diff").exists()
    assert (root / "plugins" / "p.json").exists()


def test_purge_keeps_the_codex_credentials_and_scratch_space():
    seed_codex()
    outcome = sessions.purge(APP_CODEX)

    root = paths.codex_dir()
    assert outcome["deleted"] == 4
    assert not (root / "session_index.jsonl").exists()
    assert (root / "sessions").is_dir()
    assert (root / "auth.json").exists()
    assert (root / "config.toml").exists()
    assert (root / ".tmp" / "scratch").exists()


def test_purge_on_an_empty_machine_is_a_no_op():
    outcome = sessions.purge(APP_CLAUDE)
    assert outcome == {"app": APP_CLAUDE, "deleted": 0, "freed": 0, "errors": []}


def test_an_app_with_no_session_store_is_refused():
    # Hermes has a tab, but U-Pool does not know where its transcripts live, and
    # guessing is how a delete button takes something it should not.
    with pytest.raises(UPoolError, match="does not track sessions"):
        sessions.summary(APP_HERMES)
    with pytest.raises(UPoolError, match="does not track sessions"):
        sessions.purge(APP_HERMES)


def test_a_target_outside_the_home_directory_is_refused(monkeypatch, tmp_path):
    """The guard fires on a target that escaped, not on user input.

    Nothing a user types reaches these paths - they come from a literal table - so
    the only way one can point somewhere unbounded is a path helper resolving
    somewhere unexpected, and that is what this proves is caught rather than
    deleted. Stated as a target that escapes while the home stays put, because
    moving both together is the one arrangement the guard cannot see.
    """
    escapee = tmp_path / "not-home"
    escapee.mkdir()
    (escapee / "precious.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setitem(
        sessions.TARGETS, APP_CLAUDE, (sessions.Target(lambda: escapee, True),)
    )
    with pytest.raises(UPoolError, match="outside the home directory"):
        sessions.purge(APP_CLAUDE)
    assert (escapee / "precious.txt").exists()


def test_the_home_directory_itself_is_never_the_target(monkeypatch):
    monkeypatch.setitem(
        sessions.TARGETS, APP_CLAUDE, (sessions.Target(paths.home, True),)
    )
    with pytest.raises(UPoolError, match="outside the home directory"):
        sessions.purge(APP_CLAUDE)


def test_the_endpoint_answers_with_the_refreshed_summary():
    seed_claude()
    api = Api()
    assert api.session_summary(APP_CLAUDE)["data"]["files"] == 4

    response = api.delete_sessions(APP_CLAUDE)
    assert response["ok"] is True
    assert response["data"]["deleted"] == 4
    assert response["data"]["summary"]["files"] == 0


def test_the_endpoint_reports_an_unknown_app_as_an_error():
    response = Api().delete_sessions("nope")
    assert response["ok"] is False
    assert "does not track sessions" in response["error"]
