# U-Pool

**Provider switcher for Claude Code, Codex, Hermes and OpenCode.**

Keep every endpoint, API key, and model in one place. Switch the active provider with a click. Probe reachability before you start working.

Python backend · Next.js UI · native OS webview — no Electron, no Node at runtime.

<p align="center">
  <a href="https://github.com/U-C4N/U-Pool/stargazers"><img src="https://img.shields.io/github/stars/U-C4N/U-Pool?style=for-the-badge&logo=github&color=007aff" alt="Stars" /></a>
  <a href="https://github.com/U-C4N/U-Pool/network/members"><img src="https://img.shields.io/github/forks/U-C4N/U-Pool?style=for-the-badge&logo=github&color=0a84ff" alt="Forks" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/version-0.8.0-informational?style=for-the-badge" alt="Version 0.8.0" />
</p>

## Screenshot

<p align="center">
  <img src="assets/screenshot.png" alt="U-Pool showing the Hermes provider list, five app tabs, and the installed Claude Code and Codex versions in the header" width="900" />
</p>

<p align="center">
  <sub>Five app tabs, the installed CLI versions read off the machine, and the provider picked up from an existing Hermes config on first run.</sub>
</p>

---

## Why U-Pool?

Switching between Anthropic, OpenRouter, DeepSeek, Azure, xAI, and custom relays usually means editing config files by hand. U-Pool turns that into a desktop app:

- One list of providers for **Claude Code**, **Codex**, **Hermes** and **OpenCode**
- One click to make a provider live — in the config file *and* in the Windows environment the CLIs read
- Health checks that measure latency without spending tokens
- Atomic writes, and a backup beside every file U-Pool changes
- Which version of each CLI is installed, read off the machine rather than assumed

## Features

| | |
| --- | --- |
| **Surgical writes** | A switch changes only the keys U-Pool owns — plugins, theme, hooks, MCP servers and project trust stay where they are |
| **Windows environment** | The variables the CLIs actually read are set in `HKCU\Environment` too, and only names U-Pool set are ever deleted |
| **Atomic writes** | Temp file + `os.replace`; crash mid-write cannot leave half-written config |
| **Backups** | One copy beside the original — `settings.json.backup` — refreshed on every change, switchable off in Settings |
| **First-run import** | Existing Claude / Codex config becomes a provider instead of being overwritten |
| **Reachability probe** | Plain `GET /models` — latency only, no completions, no token cost |
| **Presets** | CodeFast, Yunwu, DeepSeek, Kimi, OpenRouter, MiniMax, Z.ai, Azure, xAI, Custom, and more |
| **Official mode** | Hand control back to vendor login by clearing U-Pool-managed keys |
| **Permission switches** | Per-provider checkboxes for bypass mode, auto-accept edits, project MCP trust, Codex approvals/sandbox and live web search |
| **Claude Desktop** | A tab, preview only — providers you add there are saved, nothing is written yet |
| **Hermes & OpenCode** | Two more tabs with their own config writers — `config.yaml` is spliced section by section, `opencode.json` merged key by key |
| **Cursor pool** | A sixth tab holding Cursor accounts — paste cookies in any common form, see name, email, plan and usage per account, and switch between the ones you have signed into Cursor |
| **CLI versions** | The header reports the installed Claude Code and Codex versions; refresh re-probes the machine |
| **Delete all sessions** | Two red buttons in Settings that erase each CLI's transcripts and prompt history — and nothing else in those folders |
| **In-app updates** | Settings shows a pulsing Update button when a newer release is out, downloads it and swaps itself |
| **Launch at sign-in** | Windows on/off switch — one `HKCU\...\Run` entry, removed again when you turn it off |

## How it works

`~/.u-pool/config.json` is the source of truth. On every switch U-Pool changes only the keys it owns in each target and leaves the rest of the file as it found it. The files are not the whole story: the CLIs also read variables straight out of the Windows environment, so a switch writes there too.

| App | Target | What a switch writes |
| --- | --- | --- |
| Claude Code | `~/.claude/settings.json` | `env` block: base URL, auth token / API key, models, extras |
| Claude Code | `~/.claude/settings.json` | Only while a checkbox is ticked: `permissions.defaultMode`, `permissions.skipDangerousModePermissionPrompt`, `enableAllProjectMcpServers` |
| Claude Code | `HKCU\Environment` | The same names again: `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` *or* `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_SMALL_FAST_MODEL`, extras |
| Claude Desktop | — | Nothing yet. The provider is saved and marked current, and you get a notice saying so |
| Codex | `~/.codex/config.toml` | `model_provider`, `model`, one `[model_providers.<slug>]` table |
| Codex | `~/.codex/config.toml` | Only while a checkbox is ticked: `approval_policy`, `sandbox_mode`, `web_search` |
| Codex | `~/.codex/auth.json` | Written from scratch: `{"OPENAI_API_KEY": "…"}` when that is the provider's env key, otherwise `{}` |
| Codex | `HKCU\Environment` | Whatever the provider's `env_key` is named, plus `OPENAI_BASE_URL` |
| Hermes | `%LOCALAPPDATA%\hermes\config.yaml` | One entry under `providers:`, plus `model.provider` and `model.default` |
| OpenCode | `~/.config/opencode/opencode.json` | One entry under `provider`, plus `$schema` and the top-level `model` |
| Cursor | `%APPDATA%\Cursor\...\state.vscdb` | The `cursorAuth/*` rows in `ItemTable`, and nothing else in the database |

Hermes, OpenCode and Cursor read their credentials out of their own files, so none of them writes anything to `HKCU\Environment`.

### What a switch leaves alone

0.5.0 rebuilt `~/.claude/settings.json` and `~/.codex/config.toml` from the provider record alone. That did stop the pile-up it was aimed at — an orphan `[model_providers.*]` table, a stale `ANTHROPIC_AUTH_TOKEN` sitting beside the new one — but it also took everything else in those files with it.

Since 0.6.0 the write is surgical: U-Pool reads the file, replaces the handful of keys it owns, and puts the rest back untouched. `enabledPlugins`, `extraKnownMarketplaces`, `theme`, `effortLevel`, `model`, `hooks`, `statusLine` and `permissions.allow` / `deny` survive a Claude Code switch. `[mcp_servers.*]`, `[plugins.*]`, `[projects.*]` trust levels, `notify`, `[windows]`, `[features]`, `[shell_environment_policy]` and your comments survive a Codex switch — a key whose value has not changed is not even re-rendered, so its trailing comment stays put.

Hermes gets the same treatment, and it matters more there: `config.yaml` is one 700-line document holding the toolsets, the kanban board and the chat integrations alongside the providers. U-Pool parses it to read, then splices new text into the `model:` and `providers:` line ranges only — every other line comes back byte for byte. `opencode.json` is merged the same way, so `theme`, `mcp`, `plugin`, `agent` and `keybinds` survive.

Two things are still owned **whole**, because that is exactly where the pile-up happened:

- the `env` block in `settings.json` — rebuilt from the provider every switch, removed entirely for an official one
- `[model_providers]` in `config.toml` — only the active provider's table survives; the others are dropped and named in the toast

Hermes and OpenCode are the opposite case and are deliberately **narrower**: a switch removes only the provider entry U-Pool wrote last time, recorded in `~/.u-pool/env-owned.json`. Entries you added by hand sit in the same map and are never touched.

`~/.codex/auth.json` is written from scratch, because merging is what let a key an older build wrote sit there for months. If the file holds a ChatGPT login instead of an API key, the whole original is copied to `~/.u-pool/codex-login.json` first and restored verbatim when you switch back to the official provider.

A file that will not parse stops the switch instead of being overwritten: preserving what U-Pool did not write means reading it first.

### Windows environment

Codex resolves its key by name — `config.toml` says `env_key = "codefast"` and Codex then looks for `codefast` in its process environment, which no file U-Pool writes can supply. Claude Desktop and any shell you open are in the same position. So a switch also writes `HKCU\Environment` and broadcasts `WM_SETTINGCHANGE`, so a program started afterwards sees the change without a sign-out.

U-Pool records the names it sets in `~/.u-pool/env-owned.json` and **deletes only those**. A variable you set by hand is never removed. If a name U-Pool needs already exists it takes that name over, and the value it replaced goes to the backup first. Switching to the official provider clears the whole namespace U-Pool claimed and nothing else.

**Settings → Windows environment** lists what is managed for the current app with masked values, marks anything set outside U-Pool, and opens the Windows editor. Off Windows the whole feature is inert.

### Backups

Every file U-Pool is about to change is copied once, beside the original, as `<filename>.backup` — `settings.json.backup`, `config.toml.backup`, `auth.json.backup`. One current copy, overwritten each time, not a history. Registry values cannot sit beside anything, so their pre-change values go to `~/.u-pool/backups/environment.backup.json`, where a `null` means the name did not exist.

The switch in **Settings → Backups** turns it off, and with it off nothing is written at all. There is no restore screen: the copies are plain files, and putting one back is a rename.

### Claude Desktop

Claude Desktop is a tab and **writes nothing**. Providers you add there are saved and can be made current, and a notice above the list says the configuration was not written.

The reason it is preview rather than finished: Claude Desktop has no base-URL setting, so the only lever is the operating system environment — and that is the same `ANTHROPIC_*` namespace Claude Code already owns. Writing it here would move Claude Code's endpoint too. `%APPDATA%\Claude\claude_desktop_config.json` is never opened; it is your MCP servers and preferences, not provider config.

### Hermes

Hermes keeps everything in one `config.yaml` — under `%LOCALAPPDATA%\hermes` on Windows, `~/.hermes` elsewhere, and wherever `HERMES_HOME` points if it is set. A provider is an entry in the `providers:` map with `api`, `api_key`, `default_model`, `models` and `transport`; `model.provider` names the active one.

`transport` is the field worth getting right, because Hermes does not infer it from the URL: `anthropic_messages`, `chat_completions`, `codex_responses` or `bedrock_converse`. The preset chips set it for you.

A switch removes only the entry U-Pool wrote before. Providers you added yourself stay, and so does every other section of the file — the write is a line-range splice, not a re-serialisation.

### OpenCode

`~/.config/opencode/opencode.json` on every platform, including Windows, or wherever `OPENCODE_CONFIG` points. A provider is an entry under `provider` with `npm`, `name`, `options.baseURL`, `options.apiKey` and `models`; the top-level `model` selects it as `"<provider>/<model>"`, which is why the model id is required rather than optional here.

`npm` picks the AI SDK adapter — `@ai-sdk/anthropic` for Claude-shaped endpoints, `@ai-sdk/openai-compatible` for most relays. Headers and SDK flags you add through **Extra SDK options** are kept when the key is rotated.

### Cursor pool

The Cursor tab is a pool of accounts rather than a list of providers — a Cursor account has no base URL and no API key, only a session cookie. **Add account** takes one paste box that accepts whatever you have: a Netscape `cookies.txt` line, a bare `WorkosCursorSessionToken=…`, a raw `user_…::token`, an `email,token` CSV row, or JSON. Paste one line or two hundred; unrecognised lines are counted and skipped, so a whole browser export works as-is.

Accounts are identified by the user id in the cookie, not by email. Re-pasting a rotated cookie for an account already in the pool refreshes its token in place and keeps its position.

Every account is refreshed in the background on launch, against the same endpoints the cursor.com dashboard uses — `/api/auth/me` for name and email, `/api/auth/stripe` for the plan, `/api/usage-summary` (or the legacy `/api/usage`) for the meter. Those endpoints are undocumented and will change: a field that stops arriving renders as `—` and never breaks the switch. A rejected cookie marks the row **Expired** and disables its button; the row itself stays, so pasting a fresh cookie revives it.

**What Use needs is a session token, not any cookie.** A cookie exported from a browser (`WorkosCursorSessionToken`) authenticates the cursor.com API — which is why it fills a card with a name, plan and usage — but it is a `web` token, and writing one into `state.vscdb` makes the desktop app reject it and sign itself out. The desktop needs the `session` token Cursor writes when you sign into an account in the app itself. U-Pool banks that session automatically the moment you are signed in, so the accounts you can actually switch to are the ones you have signed into Cursor at least once (or whose session token you paste directly). Use is disabled on a browser-cookie row, with a note saying so, rather than signing you out. Turning a browser cookie into a session token — Cursor's own deep-login exchange — is proven possible and planned, but not in 0.8.0.

**Use** closes Cursor, backs up `state.vscdb` beside itself, writes the auth keys and starts Cursor again. If Cursor does not close within ten seconds, **nothing is written** — you may have unsaved work, and a half-applied auth record leaves an editor that can neither sign in nor sign out. There is no force kill.

What a switch owns in that database is the `cursorAuth/*` rows in `ItemTable`, and nothing else. `cursorDiskKV` and `composerHeaders` — your Cursor conversations — are never opened, and neither are the `mcpOAuth.secret.*` rows sitting in the same table. The telemetry machine ids in `storage.json` and `%APPDATA%\Cursor\machineId` are **not** touched: resetting those is not part of switching accounts.

Session tokens live in `~/.u-pool/cursor.json` in plain text, the same as the provider API keys in `config.json`.

### CLI versions

The header shows the installed Claude Code and Codex versions, and the refresh button re-probes for them. `shutil.which` is tried first, then the places a global install actually lands — `%APPDATA%\npm`, `%LOCALAPPDATA%\npm`, `~/.bun/bin`, `~/.local/bin` — because a window launched from Explorer or the sign-in entry inherits the PATH as it stood at logon, not the one your terminal has.

A tool found on disk that cannot report a version says so, rather than being reported as missing: a broken shim and an absent install need different fixes. **Settings → Installed CLIs** shows the resolved path for each.

### Delete all sessions

**Settings → Sessions** has one red button per CLI, with the file count and size measured before you press it. Confirming erases:

| | |
| --- | --- |
| Claude Code | `~/.claude/projects`, `~/.claude/sessions`, `~/.claude/history.jsonl` |
| Codex | `~/.codex/sessions`, `~/.codex/archived_sessions`, `~/.codex/history.jsonl`, `~/.codex/session_index.jsonl` |

The list is a fixed table, not a pattern or a scan. Both CLIs keep their transcripts in the same folder as their credentials and settings, so `settings.json`, `.credentials.json`, `auth.json`, `config.toml`, plugins, skills, `file-history` and the scratch directories are all out of scope and stay.

Nothing is backed up first — a second copy of 230 MB somewhere you did not ask for is not a safety net. Directories are emptied rather than removed, so the CLI still finds them. A file the running CLI holds open is reported as left behind instead of aborting the rest.

### Advanced options

Each checkbox in **Add provider → Advanced options** writes exactly one key while it is ticked. These keys are U-Pool's, so unticking a box deletes its key on the next switch rather than leaving it behind.

| Checkbox | App | Written |
| --- | --- | --- |
| Bypass permission prompts | Claude Code | `permissions.defaultMode = "bypassPermissions"` — the settings form of `--dangerously-skip-permissions` |
| Skip the bypass warning screen | Claude Code | `permissions.skipDangerousModePermissionPrompt = true` |
| Auto-accept file edits | Claude Code | `permissions.defaultMode = "acceptEdits"` |
| Trust MCP servers from the project | Claude Code | `enableAllProjectMcpServers = true` |
| Bypass approvals & sandbox | Codex | `approval_policy = "never"` + `sandbox_mode = "danger-full-access"` — the pair `--dangerously-bypass-approvals-and-sandbox` sets |
| Live web search | Codex | `web_search = "live"` at the root (the `[tools]` boolean form is a no-op in Codex) |

A provider with bypass on is badged **Bypass** in the list, so you can see it without opening the form. Switching to the official entry clears all of it.

### Updates (Windows)

**Settings → Updates** asks `api.github.com` for the latest release at most once every six hours, and remembers the answer so an offline launch still knows about it. When there is something newer the Update button pulses; pressing it downloads the release zip, checks it against the length and — when GitHub published one — the SHA-256, unpacks it beside the install folder, and hands over to a small detached script. That script waits for U-Pool to exit, renames the old folder aside, renames the new one into place and starts it. If the second rename fails the first is undone, so a failed update leaves the version you had.

Two situations are refused rather than half-applied, with the reason shown next to an **Open release page** button instead: running from a source checkout (`git pull` + `pip install -e .` + `npm run build` is the update path there), and an install under `Program Files`, where Windows will not let U-Pool replace itself without admin rights. Move the folder somewhere you own and the in-place install works.

You can turn the check off entirely with the switch in the same section.

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
python scripts/build.py            # → dist/U-Pool/U-Pool.exe + dist/U-Pool-<ver>-win64.zip
python scripts/build.py --skip-ui  # reuse an existing ui/out
```

Onedir (not onefile) for faster startup. The zip and the `SHA256SUMS.txt` printed alongside it are what the in-app updater downloads, so both have to be attached to the GitHub release as assets.

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
