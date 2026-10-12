from __future__ import annotations

import sqlite3
import time

from upool import paths
from upool.proxy.telemetry import Telemetry


def test_only_real_usage_is_counted_and_retention_runs_without_new_requests():
    telemetry = Telemetry()
    account = {"id": "a", "provider": "chatgpt"}
    telemetry.record("old", account, "chatgpt/test", 200, 100, {"input_tokens": 15, "output_tokens": 4})
    telemetry.record("current", account, "chatgpt/test", 499, 50)
    target = paths.proxy_dir() / "requests.sqlite3"
    connection = sqlite3.connect(target)
    try:
        connection.execute("UPDATE requests SET timestamp=? WHERE id='old'", (time.time() - 31 * 86400,))
        connection.commit()
    finally:
        connection.close()
    summary = telemetry.summary()
    assert summary == {"requests": 1, "errors": 1, "avg_latency_ms": 50, "input_tokens": None, "output_tokens": None}
    # Connections from both write and summary must be closed even on Windows.
    moved = target.with_name("requests-moved.sqlite3")
    target.rename(moved)
    assert telemetry.summary()["requests"] == 0
