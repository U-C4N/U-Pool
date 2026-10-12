# U-Pool 1.0.0 validation record

Validated on Windows x64 on 2026-10-11. This is a release candidate: the remaining live-account and clean-machine checks below still gate the v1 release.

## Automated and packaged checks

| Check | Result |
| --- | --- |
| Complete sandbox pytest suite | 817 passed, 65.21 seconds |
| UI typecheck and production build | Passed |
| Packaged desktop `--version` | Passed |
| Frozen source verification | 14 desktop modules and the worker entry point match current source |
| ZIP, manifest, worker checksums and license notices | Passed |
| Packaged worker authenticated health and isolated Chrome startup | Passed |
| Actual Grok device OAuth begin, pending and cancellation | Passed; no Grok sign-in performed |
| Owned worker, motor and Chrome shutdown | Passed |

The ZIP is `dist/U-Pool-1.0.0-win64.zip`. Its SHA-256 is `89baa68b3a202b1b46836f04f7660b9cc053b8f1eaa386f74bc4376896f0fe84`; `dist/SHA256SUMS.txt` records it. The packaged worker ZIP SHA-256 is `fcac967b18713345db1dcb754859c46cb1d4aa06dbfff1131174862b6197e862`.

## Authorized real ChatGPT account checks

The supplied cookie export was used only in isolated test homes. Credentials and account identity are omitted from this record. All four temporary cookie test homes were removed after shutdown; the original export was left unchanged. The final gateway, worker and Chrome debug endpoints were verified closed.

| Check | Result |
| --- | --- |
| Netscape cookie import and authenticated identity/model catalog | Passed |
| Reimport of the same identity without a duplicate account | Passed |
| Stop and restart worker, restore session from saved cookies | Passed |
| OpenAI SDK normal and streaming Chat Completions | Passed |
| OpenAI SDK normal and streaming Responses | Passed |
| Chat Completions: two ordered file tool calls, tool results, final answer | Passed |
| Streaming Responses: two ordered file tool calls, tool results, final answer | Passed |
| Streaming Markdown/code fences and Python source formatting | Passed |
| Real provider 429 response mapped through the gateway | Passed after worker status-observation fix |
| Real Hermes coding-client read, edit, test and continuation | Pending; the provider's conversation projection returned 429 during the attempt |

These generation checks used `chatgpt/auto`. The catalog exposed 27 concrete model identifiers plus the explicit auto alias. Catalog visibility does not prove successful web-picker selection or generation for every concrete model.

The SDK checks sent 11 top-level generation requests, with synthetic prompts and temporary source files. Invalid tool envelopes may cause one additional repair request. No existing user conversations or project files were used. The separate coding-client attempts produced no completed tool step; the final attempt was stopped after repeated provider rate limits, and its managed child processes and gateway were closed.

The current ChatGPT page requires waiting for the submitted conversation's canonical completed response to preserve Markdown. SSE is valid, but the content can arrive in one completed chunk. Token-by-token latency has not been established for this layout.

## Remaining release gates

- Complete actual coding-client acceptance after the provider's rate limit clears. Hermes was launched with its existing committed Python 3.14.7 environment and a generated provider entry. Its legacy Python 3.11 environment was incompatible with the installed source; no installed source, dependency or user configuration was changed. The final run used the supported three-tool bundle (`read_file`, `patch`, `terminal`), with auxiliary title generation disabled. It reached the real account but repeated 429 responses prevented a completed tool step; the test owner stopped the run and every managed process.
- Codex CLI 0.161.0 is incompatible with the current strict gateway Responses contract. An accountless loopback capture found unconditional `client_metadata`, `prompt_cache_key`, `reasoning={}` and encrypted-reasoning `include` fields. An explicit field-adapter contract would be needed before accepting this client; model settings do not remove all fields.
- Authenticate a real Grok account and validate normal/streaming generation and repeated tool calls.
- Validate live multi-account routing, session expiry, quotas and concurrent profile isolation with two real accounts. These behaviors have automated coverage, but only one real ChatGPT account was supplied.
- Run the final desktop package on clean Windows x64 without preinstalled Python, Node or Docker, and manually validate window close, tray open/stop/quit and update behavior.

Read [API setup and limits](api-gateway.md) before running the separate, opt-in live validator. Normal pytest and packaging never consume real credentials or send account generation requests.

## Source and documentation recheck - 2026-10-12

- Complete sandbox pytest suite: **817 passed** in 85.77 seconds. The restricted execution environment denied writes to the dedicated test registry keys; the successful run used the same redirected homes and test keys with the required local access.
- UI TypeScript check and production static build: **passed**.
- README and linked guides: local file links and Git whitespace checks passed.
- The new README screenshot was captured from the production UI export in browser preview mode with synthetic ChatGPT and Grok accounts. It shows the interface, not live account validation.

This recheck does not replace the packaged checks above or complete any remaining live-account or clean-machine release gates. The desktop ZIP was not rebuilt during this documentation update.
