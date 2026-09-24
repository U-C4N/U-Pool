"""What a token costs, which is the one figure the transcripts do not record.

The built-in numbers are dated and copied from each vendor's public pricing page;
they change, so ``pricing.json`` overlays them key by key and kind by kind, which
also lets a custom relay's model be priced without a code change. An unpriced model
costs ``None`` rather than zero - zero reads as free, ``None`` reads as unknown, and
the panel renders it as an em dash.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import atomicio, paths

# $ per 1,000,000 tokens, by kind, as indicative first-party rates on
# 2026-09-24. Vendor prices drift, so pricing.json overlays these per model
# and per kind - the panel never depends on a number here being current.
BUILTIN: dict[str, dict[str, float]] = {
    "claude-opus-5":   {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-5": {"input": 3.0,  "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-haiku":    {"input": 1.0,  "output": 5.0,  "cache_read": 0.1, "cache_write": 1.25},
    "gpt-6":           {"input": 2.5,  "output": 10.0, "cache_read": 0.25, "cache_write": 3.125},
    "codex":           {"input": 2.5,  "output": 10.0, "cache_read": 0.25, "cache_write": 3.125},
}


def _pricing_path():
    return paths.pricing_file()


@dataclass
class Pricing:
    table: dict[str, dict[str, float]] = field(default_factory=dict)
    _overrides: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Pricing":
        try:
            overrides = atomicio.read_json(_pricing_path(), default={}) or {}
        except ValueError:
            overrides = {}
        if not isinstance(overrides, dict):
            overrides = {}
        table: dict[str, dict[str, float]] = {k: dict(v) for k, v in BUILTIN.items()}
        for model, rates in overrides.items():
            if not isinstance(rates, dict):
                continue
            clean = {k: v for k, v in rates.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
            if clean:
                table.setdefault(model, {}).update(clean)
        return cls(table=table, _overrides=overrides)

    def rates(self, model: str) -> dict[str, float]:
        if model in self.table:
            return self.table[model]
        best = ""
        for key in self.table:
            if model.startswith(key) and len(key) > len(best):
                best = key
        return self.table.get(best, {})

    def cost(self, model: str, counts: dict[str, int]) -> float | None:
        rates = self.rates(model)
        if not rates:
            return None
        total = 0.0
        for kind, rate in rates.items():
            total += counts.get(kind, 0) * rate / 1_000_000
        return round(total, 6)

    def merged(self) -> dict[str, dict[str, float]]:
        return self.table

    def overrides(self) -> dict[str, dict[str, float]]:
        return self._overrides
