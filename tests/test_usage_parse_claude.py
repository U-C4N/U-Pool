from __future__ import annotations

import json
from datetime import timedelta, timezone

from upool.usage.models import FileMark
from upool.usage import parse_claude


TZ = timezone(timedelta(hours=3))  # a fixed offset makes the local date deterministic


def _line(**usage):
    return json.dumps({
        "type": "assistant",
        "timestamp": "2026-09-10T23:30:00.000Z",  # 02:30 next day at +03:00
        "cwd": "C:\\Users\\me\\Projects\\alpha",
        "message": {"model": "claude-opus-5", "usage": usage},
    })


def test_assistant_usage_folds_into_one_bucket(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(_line(input_tokens=10, output_tokens=5,
                       cache_creation_input_tokens=3, cache_read_input_tokens=7) + "\n",
                 encoding="utf-8", newline="")
    buckets, mark, skipped = parse_claude.parse_file(f, None, TZ)
    assert skipped == 0
    (key, totals), = buckets.items()
    assert key.app == "claude" and key.model == "claude-opus-5"
    assert key.date == "2026-09-11" and key.project == "alpha"  # +03:00 rolled the day
    assert (totals.input, totals.output, totals.cache_read, totals.cache_write) == (10, 5, 7, 3)
    assert totals.messages == 1


def test_non_assistant_and_missing_usage_are_skipped_not_crashed(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join([
        json.dumps({"type": "user", "message": {"content": "hi"}}),
        json.dumps({"type": "assistant", "message": {"model": "m"}}),  # no usage
        "{ this is not json",
    ]) + "\n", encoding="utf-8", newline="")
    buckets, mark, skipped = parse_claude.parse_file(f, None, TZ)
    assert buckets == {}
    assert skipped == 1  # only the unparseable line counts as skipped


def test_resume_reads_only_appended_lines(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(_line(input_tokens=10, output_tokens=0) + "\n", encoding="utf-8", newline="")
    _, mark, _ = parse_claude.parse_file(f, None, TZ)
    with f.open("a", encoding="utf-8", newline="") as fh:
        fh.write(_line(input_tokens=4, output_tokens=0) + "\n")
    buckets, mark2, _ = parse_claude.parse_file(f, mark, TZ)
    (totals,) = buckets.values()
    assert totals.input == 4          # only the appended line
    assert mark2.offset > mark.offset


def test_incomplete_final_line_is_held_for_next_pass(tmp_path):
    f = tmp_path / "t.jsonl"
    full = _line(input_tokens=10, output_tokens=0) + "\n"
    f.write_text(full + '{"type":"assistant","message":{', encoding="utf-8", newline="")  # no trailing newline
    buckets, mark, skipped = parse_claude.parse_file(f, None, TZ)
    (totals,) = buckets.values()
    assert totals.input == 10
    assert mark.offset == len(full.encode("utf-8"))  # stopped at the last newline
