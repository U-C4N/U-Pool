# tests/test_usage_service.py
from __future__ import annotations

import json
from datetime import timedelta, timezone

from upool import paths
from upool.usage import service, store

TZ = timezone(timedelta(hours=0))


def _sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir(exist_ok=True)
    monkeypatch.setenv("UPOOL_FAKE_HOME", str(home))
    monkeypatch.setenv("UPOOL_HOME", str(tmp_path / "state"))
    return home


def _claude(home, project_dir, name, body):
    d = paths.claude_dir() / "projects" / project_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")


def _cl_line(inp, out, ts="2026-09-10T12:00:00Z", model="claude-opus-5", cwd="C:\\p\\alpha"):
    return json.dumps({"type": "assistant", "timestamp": ts, "cwd": cwd,
                       "message": {"model": model, "usage": {"input_tokens": inp, "output_tokens": out}}})


def test_refresh_folds_transcripts_and_survives_deletion(tmp_path, monkeypatch):
    home = _sandbox(tmp_path, monkeypatch)
    _claude(home, "C--p-alpha", "s.jsonl", _cl_line(100, 10) + "\n")
    snap = service.refresh(tz=TZ)
    total_in = sum(t.input for t in snap.buckets.values())
    assert total_in == 100

    # Delete the source (the purge case) and rescan: buckets kept, mark dropped.
    (paths.claude_dir() / "projects" / "C--p-alpha" / "s.jsonl").unlink()
    snap2 = service.refresh(tz=TZ)
    assert sum(t.input for t in snap2.buckets.values()) == 100      # history kept
    assert snap2.files == {}                                        # mark dropped


def test_refresh_is_incremental_not_double_counted(tmp_path, monkeypatch):
    home = _sandbox(tmp_path, monkeypatch)
    _claude(home, "C--p-alpha", "s.jsonl", _cl_line(100, 10) + "\n")
    service.refresh(tz=TZ)
    # Append; a second refresh must add only the new line.
    p = paths.claude_dir() / "projects" / "C--p-alpha" / "s.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        fh.write(_cl_line(5, 0) + "\n")
    snap = service.refresh(tz=TZ)
    assert sum(t.input for t in snap.buckets.values()) == 105


def test_summary_prices_and_shapes(tmp_path, monkeypatch):
    home = _sandbox(tmp_path, monkeypatch)
    _claude(home, "C--p-alpha", "s.jsonl", _cl_line(1_000_000, 0) + "\n")
    service.refresh(tz=TZ)
    out = service.summary("all", tz=TZ)
    assert out["tiles"]["claude"]["tokens"] >= 1_000_000
    assert out["tiles"]["claude"]["cost"] is not None   # claude-opus-5 is priced
    assert any(row["model"] == "claude-opus-5" for row in out["by_model"])
