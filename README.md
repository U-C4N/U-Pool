# U-Pool

**Provider switcher for Claude Code and Codex.**

Keep every endpoint, API key, and model in one place. Switch the active provider with a click. Probe reachability before you start working.

Python backend · Next.js UI · native OS webview — no Electron, no Node at runtime.

<p align="center">
  <a href="https://github.com/U-C4N/U-Pool/stargazers"><img src="https://img.shields.io/github/stars/U-C4N/U-Pool?style=for-the-badge&logo=github&color=007aff" alt="Stars" /></a>
  <a href="https://github.com/U-C4N/U-Pool/network/members"><img src="https://img.shields.io/github/forks/U-C4N/U-Pool?style=for-the-badge&logo=github&color=0a84ff" alt="Forks" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/version-0.4.0-informational?style=for-the-badge" alt="Version 0.4.0" />
</p>

## Screenshot

<p align="center">
  <img src="assets/screenshot.png" alt="U-Pool providers view" width="900" />
</p>

---

## Why U-Pool?

Switching between Anthropic, OpenRouter, DeepSeek, Azure, xAI, and custom relays usually means editing config files by hand. U-Pool turns that into a desktop app:

- One list of providers for **Claude Code** and **Codex**
- One click to make a provider live
- Health checks that measure latency without spending tokens
- Atomic writes and rolling backups so a bad switch never bricks your setup

## Features

| | |
| --- | --- |
| **Switch safely** | Rewrites only the keys U-Pool owns — permissions, hooks, themes, and comments stay intact |
| **Atomic writes** | Temp file + `os.replace`; crash mid-write cannot leave half-written config |
| **Rolling backups** | Every overwrite lands in `~/.u-pool/backups/<app>/` (10 deep) |
| **First-run import** | Existing Claude / Codex config becomes a provider instead of being overwritten |
| **Reachability probe** | Plain `GET /models` — latency only, no completions, no token cost |
| **Presets** | Yunwu, DeepSeek, Kimi, OpenRouter, MiniMax, Z.ai, Azure, xAI, Custom, and more |
| **Official mode** | Hand control back to vendor login by clearing U-Pool-managed keys |
| **Permission switches** | Per-provider checkboxes for bypass mode, auto-accept edits, project MCP trust, Codex approvals/sandbox and live web search |
| **Launch at sign-in** | Windows on/off switch — one `HKCU\...\Run` entry, removed again when you turn it off |

## How it works

`~/.u-pool/config.json` is the source of truth. The files the CLIs read are treated as **output** — on every switch U-Pool backs them up and rewrites only what it owns:

| App | File | What U-Pool writes |
| --- | --- | --- |
| Claude Code | `~/.claude/settings.json` | `env` block: base URL, auth token / API key, models, extras |
| Claude Code | `~/.claude/settings.json` | Only while a checkbox is ticked: `permissions.defaultMode`, `permissions.skipDangerousModePermissionPrompt`, `enableAllProjectMcpServers` |
| Codex | `~/.codex/config.toml` | `model_provider`, `model`, `[model_providers.<slug>]` |
| Codex | `~/.codex/config.toml` | Only while a checkbox is ticked: `approval_policy`, `sandbox_mode`, `web_search` |
| Codex | `~/.codex/auth.json` | `OPENAI_API_KEY` only — ChatGPT login tokens are preserved |

### Advanced options

Each checkbox in **Add provider → Advanced options** owns exactly one key while it is ticked, and takes that key back out when you untick it. Your own `permissions.allow` / `deny` rules, a hand-picked `approval_policy`, and any `defaultMode` U-Pool does not write (`plan`, `auto`, …) are never touched.

| Checkbox | App | Written |
| --- | --- | --- |
| Bypass permission prompts | Claude Code | `permissions.defaultMode = "bypassPermissions"` — the settings form of `--dangerously-skip-permissions` |
| Skip the bypass warning screen | Claude Code | `permissions.skipDangerousModePermissionPrompt = true` |
| Auto-accept file edits | Claude Code | `permissions.defaultMode = "acceptEdits"` |
| Trust MCP servers from the project | Claude Code | `enableAllProjectMcpServers = true` |
| Bypass approvals & sandbox | Codex | `approval_policy = "never"` + `sandbox_mode = "danger-full-access"` — the pair `--dangerously-bypass-approvals-and-sandbox` sets |
| Live web search | Codex | `web_search = "live"` at the root (the `[tools]` boolean form is a no-op in Codex) |

A provider with bypass on is badged **Bypass** in the list, so you can see it without opening the form. Switching to the official entry clears all of it.

### Launch at sign-in (Windows)

**Settings → Startup** writes one `REG_SZ` value named `U-Pool` under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` — no admin rights, no scheduled task, no shortcut. The remembered choice lives in `~/.u-pool/settings.json`, and the entry is re-pointed at the current build on every launch so a moved bundle cannot leave a dead command behind.

If you switch U-Pool off under **Task Manager → Startup apps**, Windows keeps that decision: U-Pool reports "switched off by Windows" and offers a shortcut to the Windows page instead of quietly overriding you.

```
┌───────────────────────────────────────────────┐
│  Next.js (static export)  ── window.pywebview │  UI
├───────────────────────────────────────────────┤
│  Api  →  Store  →  Adapters  →  live files    │  Python
└───────────────────────────────────────────────┘
```

## Quick start

**Requirements:** Python 3.11+, Node 20+

```bash
pip install -e ".[dev]"
cd ui && npm install && npm run build && cd ..
python -m upool
```

### UI hot reload

```bash
# terminal 1
cd ui && npm run dev

# terminal 2
UPOOL_DEV_URL=http://localhost:3000 python -m upool
```

Open `http://localhost:3000` in a browser without the Python bridge for an in-memory mock preview.

### Tests

```bash
python -m pytest
cd ui && npm run build
```

Tests redirect `HOME` into a temp folder — your real Claude / Codex configs are never touched.

## Windows bundle

```bash
python scripts/build.py            # → dist/U-Pool/U-Pool.exe
python scripts/build.py --skip-ui  # reuse an existing ui/out
```

Onedir (not onefile) for faster startup.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `UPOOL_DEV_URL` | Load the UI from a dev server instead of the bundled export |
| `UPOOL_DEBUG=1` | Open the webview DevTools |
| `UPOOL_HOME` | Move U-Pool’s own state directory |
| `UPOOL_FAKE_HOME` | Redirect `~/.claude` / `~/.codex` lookups (used by tests) |

## Tech stack

- **Backend** — Python 3.11+, pywebview, tomlkit
- **UI** — Next.js 16 (static export), React 19, Tailwind 4, TypeScript
- **Ship** — setuptools, pytest, PyInstaller

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=U-C4N/U-Pool&type=Date)](https://star-history.com/#U-C4N/U-Pool&Date)

---

<p align="center">
  Made with care by
  <a href="https://x.com/UEdizaslan"><strong>@UEdizaslan</strong></a>
</p>
