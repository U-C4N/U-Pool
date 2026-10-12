# Use the managed API pool

U-Pool 1.0 adds a Windows x64 gateway for your ChatGPT web sessions and Grok OAuth accounts. ChatGPT coding support is experimental: the web interface has no native function-call protocol, so the gateway validates a structured prompt response and converts it into tool calls.

## Connect accounts

1. Open **API**, select **Install components**, and wait for the pinned components to finish installing. No Python, Node or Docker installation is needed in the packaged application.
2. For ChatGPT, import a cookie JSON export, Netscape `cookies.txt`, or complete `Cookie:` header. The export must include the authenticated session cookie; `document.cookie` alone omits HttpOnly cookies. U-Pool verifies identity before enabling the account.
3. For Grok, select its login action, authorize the device code in your browser, and wait for completion. Account authentication and model availability are checked separately.
4. Start the API, copy its client key, and choose a model shown in the pool. Model identifiers start with `chatgpt/` or `grok/`.
5. Create a Codex, Hermes or OpenCode provider from the API panel, then activate it with the target application's existing **Use** action.

Each account has its own managed profile/auth directory. Reimporting the same verified identity updates its existing account. A ChatGPT profile can be opened for login recovery. Up to two managed ChatGPT browsers run at once; requests are serial within each account.

Generated Hermes ChatGPT profiles declare that this model supports tools but has no reasoning or vision API controls. Existing explicit Hermes reasoning settings, global or per model, must be cleared in Hermes for this profile; U-Pool reports the conflict when applying it. User-supplied model capability overrides are preserved.

For the tested Hermes client, disable its auxiliary title generator in Hermes if it requests reasoning controls or structured output. U-Pool preserves this global user setting. The isolated coding acceptance uses this configuration and the client's supported custom toolset containing `read_file`, `patch`, and `terminal`:

```yaml
auxiliary:
  title_generation:
    enabled: false
    model_upgrade_enabled: false
```

Codex CLI 0.161.0 is currently incompatible with this gateway's strict Responses contract. An isolated request capture confirmed that it sends `client_metadata`, `prompt_cache_key`, empty `reasoning`, and `include=["reasoning.encrypted_content"]` even for a custom provider. Its supported configuration does not remove all these fields. Creating a provider profile does not establish working Codex generation; these requests return an explicit unsupported-field error. See the [validation record](v1-validation.md).

`chatgpt/auto` uses the managed profile's provider-chosen default model. Concrete ChatGPT models require a verified selection in the web model picker; ambiguous labels are rejected instead of substituting another model. The picker supports localized labels and confirms its checked state before generation.

## Call the gateway

The default endpoint is `http://127.0.0.1:8318/v1`. Copy the key and an available model from the API panel before running this PowerShell example:

```powershell
$proxyKey = Read-Host 'U-Pool API key'
$proxyModel = Read-Host 'Model ID from the API panel'
$proxyHeaders = @{ Authorization = "Bearer $proxyKey" }
Invoke-RestMethod -Uri http://127.0.0.1:8318/v1/models -Headers $proxyHeaders
$proxyBody = @{ model = $proxyModel; messages = @(@{ role = 'user'; content = 'Reply with OK' }); stream = $false } | ConvertTo-Json -Depth 8
Invoke-RestMethod -Uri http://127.0.0.1:8318/v1/chat/completions -Method Post -Headers $proxyHeaders -ContentType application/json -Body $proxyBody
```

Compatible clients can use `GET /v1/models`, `POST /v1/chat/completions`, and `POST /v1/responses`. HTTP streaming is supported. Responses calls are stateless: send the complete conversation, including function calls and their outputs. Stateful response storage, `previous_response_id`, built-in tools, multimodal input, and WebSocket transport are outside v1 and return explicit errors.

On ChatGPT's current web layout, U-Pool buffers the rendered answer until its own conversation's canonical response is complete, preserving Markdown and code fences. HTTP streaming still emits standard SSE events, but the answer may arrive in a single content chunk. Grok streams through its motor.

Grok's supported generation controls pass through to its motor. ChatGPT controls that its browser cannot enforce, including sampling and token-limit settings, are rejected. With ChatGPT tools enabled, output is buffered until the tool envelope and argument schemas validate. Invalid output receives one repair attempt; the gateway never executes the tools itself.

## Recover an account or stop the API

An expired session is excluded until login recovery or a fresh cookie import. A rate-limited account waits for `Retry-After`, or 60 seconds when no wait was reported. Requests may move to another eligible account before output starts. A partially emitted response is never replayed on another account.

Closing the Windows window keeps U-Pool in the tray. The tray menu opens the window, starts/stops the API, or quits. Quit and application updates stop the managed workers and their Chrome processes. Other browser profiles are not closed. Stop the API before changing the port; a port conflict is reported without silently changing the endpoint.

## State and build artifacts

`UPOOL_HOME` redirects all API state. Defaults are `~/.u-pool/proxy/` for accounts, isolated profiles and the request database, and `~/.u-pool/runtimes/` for installed components. Account/session files contain credentials; public account summaries expose no cookie/token values. The request database retains 30 days of account/model/status/latency metadata and actual usage when supplied, without prompt or response contents. Unknown tokens stay unknown; subscription usage is not reported as an API bill.

The source manifest pins CLIProxyAPI 8.0.23, Chrome for Testing 155.0.8059.39 and Web2API commit `497527dceabfa3f95961e23c291e618c5570f1ac`. Building the isolated worker generates its actual ZIP checksum. The desktop package includes that worker archive, the generated manifest and license notices. Runtime installation verifies hashes and archive paths before activating a version.

```powershell
python scripts/prepare_proxy_runtimes.py
python scripts/build.py --skip-ui --reuse-proxy-worker
python -m pytest --basetemp=.pytest-tmp -p no:cacheprovider
```

The worker build dependencies are isolated from the desktop runtime. Third-party provenance and notices are in `THIRD_PARTY_LICENSES.txt` and inside the worker package.

## Validate before publishing

Automated tests use fake upstreams and redirected homes, profiles and registry keys. They cover parsing, routing, pre-output failover, streaming cancellation, Responses translation, tool-call validation, installation failures and credential redaction.

Real-account release validation is a separate opt-in action. Use fresh test accounts in the API panel, then verify normal and streaming calls with an OpenAI-compatible client, a multi-step coding tool loop, cookie expiry, Grok login cancellation, and account isolation. Also run the packaged app on a clean Windows x64 machine without Python/Node/Docker. Passing mocked tests or a worker `--version` check does not establish these live-account results.

After connecting accounts, run `python scripts/validate_proxy_live.py --model chatgpt/<exact-model> --model grok/<exact-model>` with model IDs copied from the panel. It asks for the local client key without echoing it, exercises the actual SDK, and uses a temporary coding workspace for two rounds of function results. It sends real account requests and is deliberately excluded from the normal test/build suite. Codex, Hermes or OpenCode acceptance still requires an explicit run in that application with the created provider.
