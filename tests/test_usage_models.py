from __future__ import annotations

from upool.usage.models import KINDS, BucketKey, Totals, merge_totals


def test_totals_add_accumulates_each_kind():
    t = Totals()
    t.add(1, {"input": 10, "output": 5, "cache_read": 2, "cache_write": 1})
    t.add(2, {"input": 4, "output": 0, "cache_read": 0, "cache_write": 3})
    assert t.messages == 3
    assert t.input == 14 and t.output == 5 and t.cache_read == 2 and t.cache_write == 4


def test_totals_add_tolerates_missing_kinds():
    t = Totals()
    t.add(1, {"output": 7})  # a kind absent from the dict counts as zero
    assert t.output == 7 and t.input == 0


def test_merge_totals_sums_by_key():
    a = {BucketKey("claude", "2026-09-01", "m", "p"): Totals()}
    a[BucketKey("claude", "2026-09-01", "m", "p")].add(1, {"input": 3})
    b = {BucketKey("claude", "2026-09-01", "m", "p"): Totals()}
    b[BucketKey("claude", "2026-09-01", "m", "p")].add(1, {"input": 4})
    merge_totals(a, b)
    assert a[BucketKey("claude", "2026-09-01", "m", "p")].input == 7
    assert a[BucketKey("claude", "2026-09-01", "m", "p")].messages == 2


def test_kinds_are_the_four_the_spec_names():
    assert KINDS == ("input", "output", "cache_read", "cache_write")
