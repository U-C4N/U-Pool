"""End-to-end check of the 0.7.0 write paths, against a copy of a real config.

Run through the sandbox, never directly:

    python scripts/sandbox.py scripts/probe_0_7_0.py

It copies the machine's live ``config.yaml`` into the sandbox if there is one -
reading it is fine, and a 700-line document with real sections in it is a far
better test than a fixture - then switches providers and reports what changed.
"""

from __future__ import annotations

import difflib
import os
import sys
from pathlib import Path

from upool import adapters, paths, sessions, yamlio
from upool.models import APP_HERMES, APP_OPENCODE, Provider

FALLBACK = """\
model:
  default: moonshotai/kimi-k3-free
  provider: tokenrouter
  context_length: 200000
providers:
  tokenrouter:
    api: https://api.tokenrouter.io
    api_key: sk-router
    name: tokenrouter
    transport: chat_completions
toolsets:
  default:
    - read
    - write
slack:
  bot_token: xoxb-secret
timezone: ''
"""


def live_hermes_config() -> str:
    """The user's own config.yaml, read only, or a stand-in shaped like it."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        real = Path(local) / "hermes" / "config.yaml"
        if real.is_file():
            print(f"[probe] using a copy of {real}")
            return real.read_text(encoding="utf-8")
    print("[probe] no live Hermes config found - using the built-in stand-in")
    return FALLBACK


def report_diff(before: str, after: str) -> list[str]:
    return [
        line
        for line in difflib.unified_diff(
            before.splitlines(), after.splitlines(), "before", "after", lineterm="", n=0
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


def check_hermes() -> bool:
    path = paths.hermes_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    before = live_hermes_config()
    path.write_text(before, encoding="utf-8")

    parsed_before = yamlio.read(path)
    names_before = set(parsed_before.get("providers") or {})

    adapter = adapters.get(APP_HERMES)
    adapter.apply(
        Provider(
            app=APP_HERMES,
            name="Yunwu",
            base_url="https://api.yunwu.cloud",
            api_key="sk-probe",
            model="claude-sonnet-4-5",
        )
    )

    after = path.read_text(encoding="utf-8")
    parsed_after = yamlio.read(path)
    names_after = set(parsed_after.get("providers") or {})

    ok = True
    lost = names_before - names_after
    if lost:
        print(f"[FAIL] hermes dropped providers the user wrote: {sorted(lost)}")
        ok = False
    if "yunwu" not in names_after:
        print("[FAIL] hermes did not write the provider entry")
        ok = False
    if parsed_after.get("model", {}).get("provider") != "yunwu":
        print("[FAIL] hermes did not select the new provider")
        ok = False

    # Every top-level section other than the two U-Pool owns must be identical.
    owned = {"model", "providers"}
    for key in set(yamlio.sections(before)) - owned:
        start, end = yamlio.sections(before)[key]
        original = "\n".join(before.splitlines()[start:end])
        if key not in yamlio.sections(after):
            print(f"[FAIL] hermes lost the '{key}' section entirely")
            ok = False
            continue
        s2, e2 = yamlio.sections(after)[key]
        if "\n".join(after.splitlines()[s2:e2]) != original:
            print(f"[FAIL] hermes rewrote the '{key}' section")
            ok = False

    diff = report_diff(before, after)
    print(f"[probe] hermes: {len(before.splitlines())} lines in, {len(diff)} changed")
    for line in diff[:14]:
        print(f"        {line}")
    if len(diff) > 14:
        print(f"        ... and {len(diff) - 14} more")

    # Switching again must take back the first entry and nothing else.
    adapter.apply(
        Provider(
            app=APP_HERMES,
            name="Kimi",
            base_url="https://api.kimi.com/coding",
            api_key="sk-probe-2",
            model="kimi-for-coding",
        )
    )
    final = set(yamlio.read(path).get("providers") or {})
    if final != (names_before | {"kimi"}):
        print(f"[FAIL] hermes second switch left {sorted(final)}")
        ok = False
    else:
        print(f"[probe] hermes after two switches: {sorted(final)}")
    return ok


def check_opencode() -> bool:
    import json

    path = paths.opencode_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    before = {
        "theme": "tokyonight",
        "provider": {"mine": {"npm": "@ai-sdk/openai", "options": {"apiKey": "sk-mine"}}},
        "mcp": {"fs": {"type": "local"}},
        "keybinds": {"leader": "ctrl+x"},
    }
    path.write_text(json.dumps(before, indent=2), encoding="utf-8")

    adapters.get(APP_OPENCODE).apply(
        Provider(
            app=APP_OPENCODE,
            name="OpenCode Go",
            base_url="https://opencode.ai/zen/go/v1",
            api_key="sk-probe",
            model="deepseek-v4-flash",
            npm="@ai-sdk/openai-compatible",
        )
    )
    after = json.loads(path.read_text(encoding="utf-8"))

    ok = True
    for key in ("theme", "mcp", "keybinds"):
        if after.get(key) != before[key]:
            print(f"[FAIL] opencode changed '{key}'")
            ok = False
    if after["provider"].get("mine") != before["provider"]["mine"]:
        print("[FAIL] opencode changed the user's own provider entry")
        ok = False
    if after.get("model") != "opencode_go/deepseek-v4-flash":
        print(f"[FAIL] opencode selection is {after.get('model')!r}")
        ok = False
    print(f"[probe] opencode keys: {sorted(after)}; providers {sorted(after['provider'])}")
    return ok


def check_sessions() -> bool:
    root = paths.claude_dir()
    (root / "projects" / "repo").mkdir(parents=True, exist_ok=True)
    (root / "projects" / "repo" / "a.jsonl").write_text("x" * 500, encoding="utf-8")
    (root / "history.jsonl").write_text("y" * 40, encoding="utf-8")
    (root / "settings.json").write_text('{"env": {}}', encoding="utf-8")
    (root / ".credentials.json").write_text('{"token": "keep"}', encoding="utf-8")

    summary = sessions.summary("claude")
    outcome = sessions.purge("claude")
    after = sessions.summary("claude")

    ok = True
    if summary["files"] != 2 or outcome["deleted"] != 2 or after["files"] != 0:
        print(f"[FAIL] sessions counted {summary['files']}, deleted {outcome['deleted']}")
        ok = False
    if not (root / "projects").is_dir():
        print("[FAIL] sessions removed the projects directory itself")
        ok = False
    for keep in ("settings.json", ".credentials.json"):
        if not (root / keep).exists():
            print(f"[FAIL] sessions deleted {keep}")
            ok = False
    print(f"[probe] sessions: {outcome['deleted']} files, {outcome['freed']} bytes freed")
    return ok


def main() -> int:
    print(f"[probe] hermes config -> {paths.hermes_config_file()}")
    print(f"[probe] opencode config -> {paths.opencode_config_file()}")
    results = [check_hermes(), check_opencode(), check_sessions()]
    if all(results):
        print("\n[probe] all checks passed")
        return 0
    print("\n[probe] SOMETHING FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
