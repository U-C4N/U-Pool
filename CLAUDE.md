# Working in this repository

U-Pool rewrites configuration files that other programs are using while you work:
`~/.claude/settings.json`, `~/.codex/config.toml`, `~/.codex/auth.json`, and the
user's `HKCU\Environment` registry key. On a developer's machine those are the
same files their own Claude Code and Codex are reading right now.

## Never run a write path against the real home

`paths.home()` falls back to `Path.home()` whenever `UPOOL_FAKE_HOME` is unset.
So anything that imports `upool` and reaches `apply()`, `winenv.apply()` or
`backup.sidecar()` reconfigures this machine - including a throwaway one-liner
written to check a hypothesis.

This is not theoretical. A script probing whether a provider whose `extra` map
contained `Path` could clobber the real `PATH` wrote `ANTHROPIC_BASE_URL` pointing
at a placeholder host into a live `settings.json`. Every Anthropic request on the
machine then failed DNS resolution, which killed three agents mid-run and the
session's own tooling with them. The user had to recover their credentials by hand.

**Verify through the sandbox instead:**

```bash
python scripts/sandbox.py -c "from upool import adapters, models; ..."
python scripts/sandbox.py probe.py
python scripts/sandbox.py                 # interactive
```

It redirects `UPOOL_FAKE_HOME`, `UPOOL_HOME` and `winenv.ENV_KEY` into a fresh
temp directory and a scratch registry key, then runs your code.

`paths.home()` raises rather than resolving the real home when
`PYTEST_CURRENT_TEST` is set, so a test that escapes the `sandbox` fixture fails
loudly. Nothing detects a plain `python -c`; that is what the script above is for.

## Reading live config is fine

Inspecting the user's actual files to understand a problem is normal and useful -
`Read`, `Get-Content`, `cat`. It is *writing* that needs the sandbox.

## Tests

```bash
python -m pytest --basetemp=<a writable dir> -q
```

The suite errors during fixture setup without an explicit `--basetemp`.

The autouse `sandbox` fixture in `tests/conftest.py` redirects every target a
switch touches: both path env vars, `autostart.RUN_KEY`, `autostart.APPROVED_KEY`
and `winenv.ENV_KEY`. If you add a new write target, redirect it there in the same
commit.

## Ownership is the load-bearing idea

U-Pool shares every place it writes with someone else, so it removes only what it
recorded as its own:

- `upool.claims` keeps the record, per namespace.
- `winenv` removes only claimed registry names. The user's own `MINIMAX_CN_API_KEY`,
  `YUNWU_API_KEY` and `BLANKAPI_API_KEY` live in the same key.
- The Claude adapter owns `MANAGED_KEYS` plus its claimed pass-through names inside
  the `env` block of `settings.json`, and nothing else in it. A user who put
  `ANTHROPIC_AUTH_TOKEN` there by hand keeps it.
- Both adapters own a named set of keys in the CLI's config file and preserve every
  other key. `enabledPlugins`, `theme`, `[mcp_servers.*]`, `[projects.*]` are the
  CLI's state, not U-Pool's.

Widening any of those claims is a data-loss change. Say so out loud before making one.

## Style

Docstrings explain why a decision was made, not what the code does. Comments are
sparse, full sentences, and only where a reader would otherwise wonder why. Prose
uses ` - ` as an aside separator. Python targets 3.11+ with `from __future__ import
annotations`. No new dependencies without asking.
