from __future__ import annotations

import json

from upool import claims, paths


def test_a_claim_round_trips():
    claims.write("anthropic", ["ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"])
    assert claims.read("anthropic") == ["ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"]


def test_namespaces_do_not_see_each_other():
    claims.write("anthropic", ["ANTHROPIC_BASE_URL"])
    claims.write("openai", ["OPENAI_API_KEY"])

    assert claims.read("anthropic") == ["ANTHROPIC_BASE_URL"]
    assert claims.read("openai") == ["OPENAI_API_KEY"]
    assert claims.read("nothing-here") == []


def test_writing_one_namespace_leaves_the_others_alone():
    claims.write("anthropic", ["ANTHROPIC_BASE_URL"])
    claims.write("openai", ["OPENAI_API_KEY"])
    claims.write("anthropic", [])

    assert claims.read("openai") == ["OPENAI_API_KEY"]


def test_duplicates_collapse_case_insensitively_keeping_what_was_written():
    # Registry names are case-insensitive, so two spellings are one claim - and the
    # spelling that survives is the one the caller actually wrote.
    claims.write("anthropic", ["Anthropic_Base_Url", "ANTHROPIC_BASE_URL"])
    assert claims.read("anthropic") == ["Anthropic_Base_Url"]


def test_a_damaged_record_owns_nothing():
    """The safe direction. Owning nothing removes nothing; owning too much deletes."""
    paths.env_owned_file().parent.mkdir(parents=True, exist_ok=True)
    paths.env_owned_file().write_text("{not json", encoding="utf-8")
    assert claims.read("anthropic") == []


def test_a_record_of_the_wrong_shape_owns_nothing():
    paths.env_owned_file().parent.mkdir(parents=True, exist_ok=True)
    paths.env_owned_file().write_text(json.dumps({"anthropic": "ANTHROPIC_BASE_URL"}), encoding="utf-8")
    assert claims.read("anthropic") == []
