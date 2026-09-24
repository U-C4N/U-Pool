from __future__ import annotations

from upool import atomicio, paths
from upool.usage.pricing import BUILTIN, Pricing


def test_exact_key_beats_prefix():
    p = Pricing(table={"claude-opus-5": {"input": 15.0}, "claude-opus-5-5": {"input": 20.0}})
    assert p.rates("claude-opus-5-5")["input"] == 20.0


def test_longest_prefix_matches_when_no_exact_key():
    p = Pricing(table={"claude-opus-5": {"input": 15.0}})
    assert p.rates("claude-opus-5-5-20990101")["input"] == 15.0


def test_cost_sums_over_priced_kinds():
    p = Pricing(table={"m": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}})
    # 1e6 input @3 + 1e6 output @15 = 18.0
    assert p.cost("m", {"input": 1_000_000, "output": 1_000_000}) == 18.0


def test_unpriced_model_costs_none_but_is_still_a_model():
    p = Pricing(table={})
    assert p.cost("mystery-relay", {"input": 500}) is None


def test_partial_override_overlays_only_named_kinds(tmp_path, monkeypatch):
    monkeypatch.setenv("UPOOL_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("UPOOL_FAKE_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    atomicio.write_json(paths.app_home() / "pricing.json", {"claude-opus-5": {"output": 99.0}})
    p = Pricing.load()
    rates = p.rates("claude-opus-5")
    assert rates["output"] == 99.0                 # overridden
    assert rates["input"] == BUILTIN["claude-opus-5"]["input"]  # built-in kept


def test_load_without_override_file_is_just_builtin(tmp_path, monkeypatch):
    monkeypatch.setenv("UPOOL_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("UPOOL_FAKE_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    p = Pricing.load()
    assert p.rates("claude-opus-5") == BUILTIN["claude-opus-5"]
