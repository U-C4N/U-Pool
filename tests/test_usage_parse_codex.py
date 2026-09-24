# tests/test_usage_parse_codex.py
from __future__ import annotations

import json
from datetime import timedelta, timezone

from upool.usage import parse_codex

TZ = timezone(timedelta(hours=0))


def _meta(cwd, ts="2026-08-10T10:00:00.000Z"):
    return json.dumps({"type": "session_meta", "timestamp": ts,
                       "payload": {"cwd": cwd, "timestamp": ts}})

def _turn(model):
    return json.dumps({"type": "turn_context", "payload": {"model": model}})

def _tokens(inp, cached, cwrite, out, reasoning):
    return json.dumps({"type": "event_msg", "payload": {"type": "token_count",
        "info": {"last_token_usage": {"input_tokens": inp, "cached_input_tokens": cached,
                 "cache_write_input_tokens": cwrite, "output_tokens": out,
                 "reasoning_output_tokens": reasoning}}}})


def test_tokens_attributed_to_current_turn_model_and_input_excludes_cached(tmp_path):
    f = tmp_path / "r.jsonl"
    f.write_text("\n".join([
        _meta("C:\\work\\beta"),
        _turn("gpt-6-astra"),
        _tokens(inp=100, cached=40, cwrite=0, out=10, reasoning=5),
    ]) + "\n", encoding="utf-8")
    buckets, mark, skipped = parse_codex.parse_file(f, None, TZ)
    (key, t), = buckets.items()
    assert key.model == "gpt-6-astra" and key.project == "beta" and key.date == "2026-08-10"
    assert t.input == 60          # 100 - 40 cached
    assert t.cache_read == 40
    assert t.output == 15         # 10 + 5 reasoning
    assert skipped == 0


def test_in_session_model_change_splits_buckets(tmp_path):
    f = tmp_path / "r.jsonl"
    f.write_text("\n".join([
        _meta("C:\\work\\beta"),
        _turn("codex-auto-review"), _tokens(10, 0, 0, 1, 0),
        _turn("gpt-6-astra"),       _tokens(20, 0, 0, 2, 0),
    ]) + "\n", encoding="utf-8")
    buckets, _, _ = parse_codex.parse_file(f, None, TZ)
    by_model = {k.model: t for k, t in buckets.items()}
    assert by_model["codex-auto-review"].input == 10
    assert by_model["gpt-6-astra"].input == 20


def test_carry_preserves_model_across_a_resume(tmp_path):
    f = tmp_path / "r.jsonl"
    f.write_text("\n".join([_meta("C:\\work\\beta"), _turn("gpt-6-astra")]) + "\n", encoding="utf-8")
    _, mark, _ = parse_codex.parse_file(f, None, TZ)     # turn_context consumed, no tokens yet
    with f.open("a", encoding="utf-8") as fh:
        fh.write(_tokens(30, 0, 0, 3, 0) + "\n")          # token_count appended in a later pass
    buckets, _, _ = parse_codex.parse_file(f, mark, TZ)
    (key, t), = buckets.items()
    assert key.model == "gpt-6-astra"                     # NOT "unknown"
    assert t.input == 30
