# tests/test_usage_bridge.py
from __future__ import annotations

import json

from upool import paths, sessions
from upool.api import Api
from upool.models import APP_CLAUDE


def _claude_line(home):
    d = paths.claude_dir() / "projects" / "C--p-alpha"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s.jsonl").write_text(json.dumps({
        "type": "assistant", "timestamp": "2026-09-10T12:00:00Z", "cwd": "C:\\p\\alpha",
        "message": {"model": "claude-opus-5", "usage": {"input_tokens": 100, "output_tokens": 10}},
    }) + "\n", encoding="utf-8")


def test_usage_refresh_then_summary_reports_tokens(sandbox):
    _claude_line(sandbox)
    api = Api()
    res = api.usage_refresh("all")
    assert res["ok"] is True
    assert res["data"]["tiles"]["claude"]["all_time"]["tokens"] >= 110


def test_set_pricing_overrides_are_read_back(sandbox):
    api = Api()
    api.set_pricing({"my-relay": {"input": 1.0, "output": 2.0}})
    got = api.get_pricing()
    assert got["data"]["overrides"]["my-relay"]["output"] == 2.0


def test_delete_sessions_refreshes_usage_before_purge(sandbox, monkeypatch):
    _claude_line(sandbox)
    order = []
    from upool.usage import service as usage_service
    real_refresh = usage_service.refresh
    real_purge = sessions.purge
    monkeypatch.setattr(usage_service, "refresh", lambda *a, **k: order.append("refresh") or real_refresh(*a, **k))
    monkeypatch.setattr(sessions, "purge", lambda app: order.append("purge") or real_purge(app))
    Api().delete_sessions(APP_CLAUDE)
    assert order == ["refresh", "purge"]  # usage banked before its source is deleted
