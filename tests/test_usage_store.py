# tests/test_usage_store.py
from __future__ import annotations

from upool import atomicio, paths
from upool.usage import store
from upool.usage.models import BucketKey, FileMark, Totals


def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("UPOOL_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("UPOOL_FAKE_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)


def test_missing_snapshot_loads_empty(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    snap = store.load()
    assert snap.buckets == {} and snap.files == {}


def test_round_trips_buckets_and_marks(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    snap = store.Snapshot()
    snap.buckets[BucketKey("claude", "2026-09-01", "m", "p")] = Totals(messages=1, input=10)
    snap.files["/x.jsonl"] = FileMark(size=5, mtime=1.0, offset=5, carry="c")
    store.save(snap)
    back = store.load()
    assert back.buckets[BucketKey("claude", "2026-09-01", "m", "p")].input == 10
    assert back.files["/x.jsonl"].carry == "c" and back.files["/x.jsonl"].offset == 5


def test_wrong_version_is_treated_as_absent(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    atomicio.write_json(paths.app_home() / "usage.json", {"version": 999, "buckets": [], "files": {}})
    assert store.load().buckets == {}


def test_malformed_snapshot_does_not_raise(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    (paths.app_home()).mkdir(parents=True, exist_ok=True)
    (paths.app_home() / "usage.json").write_text("{ not json", encoding="utf-8")
    assert store.load().buckets == {}
