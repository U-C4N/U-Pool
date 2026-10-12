"""Request metadata without conversation contents or subscription price guesses."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from typing import Any

from .. import paths


class Telemetry:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        directory = paths.proxy_dir()
        directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(directory / "requests.sqlite3", timeout=10)
        connection.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, timestamp REAL, account_id TEXT, provider TEXT, model TEXT, status INTEGER, latency_ms REAL, input_tokens INTEGER, output_tokens INTEGER)")
        return connection

    def record(self, request_id: str, account: dict[str, Any], model: str, status: int,
               latency_ms: float, usage: dict[str, Any] | None = None) -> None:
        usage = usage or {}
        def counter(*names: str) -> int | None:
            value = next((usage[n] for n in names if n in usage), None)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO requests VALUES (?,?,?,?,?,?,?,?,?)", (
                request_id, time.time(), account["id"], account["provider"], model, status,
                latency_ms, counter("input_tokens", "prompt_tokens"), counter("output_tokens", "completion_tokens")))
            db.execute("DELETE FROM requests WHERE timestamp < ?", (time.time() - 30 * 86400,))

    def summary(self) -> dict[str, Any]:
        if not (paths.proxy_dir() / "requests.sqlite3").exists():
            return {"requests": 0, "errors": 0, "avg_latency_ms": 0, "input_tokens": None, "output_tokens": None}
        with self._lock, closing(self._connect()) as db, db:
            db.execute("DELETE FROM requests WHERE timestamp < ?", (time.time() - 30 * 86400,))
            row = db.execute("SELECT COUNT(*), COALESCE(SUM(status >= 400),0), COALESCE(AVG(latency_ms),0), SUM(input_tokens), SUM(output_tokens) FROM requests").fetchone()
            return dict(zip(("requests", "errors", "avg_latency_ms", "input_tokens", "output_tokens"), row))
