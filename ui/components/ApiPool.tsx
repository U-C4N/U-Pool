"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { backend } from "@/lib/bridge";
import type { AppId, AppState, ProxyAccount, ProxyLogin, ProxyState } from "@/lib/types";
import type { ToastKind } from "./Toast";
import { CopyIcon, PencilIcon, EyeIcon, EyeOffIcon, FileIcon, PlusIcon, RefreshIcon, TrashIcon } from "./icons";
import { Button, Field, IconButton, Modal, Select, Switch, TextInput, Tip, cx } from "./ui";

const STATUS: Record<string, string> = {
  verifying: "Verifying", ready: "Ready", busy: "Busy", cooldown: "Quota cooldown",
  reauth: "Sign in again", error: "Error", pending: "Waiting for sign-in",
};
const RUNTIME_NAMES: Record<string, string> = {
  cliproxyapi: "Grok engine", chrome: "Chrome for Testing", chatgpt_worker: "ChatGPT worker",
  "chrome-for-testing": "Chrome for Testing", "chatgpt-worker": "ChatGPT worker",
  grok: "Grok engine", chatgpt: "ChatGPT worker",
};
const APP_LABELS: Record<string, string> = { codex: "Codex", hermes: "Hermes", opencode: "OpenCode" };
const RUNTIME_STATUS: Record<string, string> = {
  installed: "Installed", missing: "Not installed", downloading: "Downloading",
  verifying: "Verifying", installing: "Installing", error: "Error",
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function timestamp(value: number): string {
  return value ? new Date(value < 1e12 ? value * 1000 : value).toLocaleString() : "Never";
}

function accountName(account: ProxyAccount): string {
  return account.name || account.email || `${account.provider === "chatgpt" ? "ChatGPT" : "Grok"} account`;
}

export function ApiPool({ onToast, onConnect, active = true }: {
  onToast: (kind: ToastKind, title: string, detail?: string) => void;
  onConnect: (app: AppId, state: AppState) => void;
  active?: boolean;
}) {
  const [state, setState] = useState<ProxyState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [action, setAction] = useState("");
  const [port, setPort] = useState("8318");
  const [key, setKey] = useState("");
  const [revealKey, setRevealKey] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [cookieText, setCookieText] = useState("");
  const [importName, setImportName] = useState("");
  const [importError, setImportError] = useState("");
  const [grokOpen, setGrokOpen] = useState(false);
  const [grokName, setGrokName] = useState("");
  const [login, setLogin] = useState<ProxyLogin | null>(null);
  const [loginError, setLoginError] = useState("");
  const [pendingDelete, setPendingDelete] = useState<ProxyAccount | null>(null);
  const [rename, setRename] = useState<ProxyAccount | null>(null);
  const [newName, setNewName] = useState("");
  const [connectApp, setConnectApp] = useState<AppId>("hermes");
  const [model, setModel] = useState("");
  const picker = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const polling = useRef(false);
  const busy = Boolean(action || state?.busy);

  useEffect(() => {
    let cancelled = false;
    backend.proxyState().then((next) => {
      if (cancelled) return;
      setState(next);
      setPort(String(next.port));
    }).catch((error) => !cancelled && setLoadError(message(error)));
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if ((!active && !state?.busy) || !state) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      if (polling.current || action) return;
      polling.current = true;
      const current = generation.current;
      try {
        const next = await backend.proxyState();
        if (!cancelled && current === generation.current) setState(next);
      } catch (error) {
        if (!cancelled) setLoadError(message(error));
      } finally {
        polling.current = false;
      }
    }, state.busy ? 600 : 2000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [active, state?.busy, Boolean(state), action]);

  useEffect(() => {
    if (!login) return;
    let cancelled = false;
    let inFlight = false;
    const timer = setInterval(async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const result = await backend.proxyLoginStatus(login.account_id, login.state);
        if (cancelled) return;
        if (result.state) setState(result.state);
        if (result.status === "pending") return;
        setLogin(null);
        if (result.status === "ok") {
          setGrokOpen(false);
          onToast("success", "Grok account connected");
        } else {
          setLoginError(result.error || (result.status === "cancelled" ? "Sign-in was cancelled." : "Sign-in failed. Try again."));
        }
      } catch (error) {
        if (!cancelled) { setLoginError(message(error)); setLogin(null); }
      } finally {
        inFlight = false;
      }
    }, 1200);
    return () => { cancelled = true; clearInterval(timer); };
  }, [login, onToast]);

  const run = useCallback(async <T,>(id: string, fn: () => Promise<T>): Promise<T | null> => {
    generation.current += 1;
    setAction(id);
    try {
      const result = await fn();
      setLoadError("");
      return result;
    } catch (error) {
      onToast("error", message(error));
      return null;
    } finally {
      setAction("");
    }
  }, [onToast]);

  const update = async (id: string, fn: () => Promise<ProxyState>) => {
    const next = await run(id, fn);
    if (next) setState(next);
  };

  const copy = async (value: string, label: string) => {
    const copied = await run("copy", async () => {
      await navigator.clipboard.writeText(value);
      return true;
    });
    if (copied) onToast("success", `${label} copied`);
  };

  const fetchKey = async (copyAfter: boolean) => {
    const result = await run("key", backend.proxyApiKey);
    if (!result) return;
    setKey(result.key);
    if (copyAfter) await copy(result.key, "API key");
    else setRevealKey(true);
  };

  const importCookie = async () => {
    if (!cookieText.trim()) { setImportError("Paste a cookie export or choose a file first."); return; }
    generation.current += 1;
    setAction("import");
    setImportError("");
    try {
      const result = await backend.proxyImportCookie(cookieText, importName.trim());
      setState(result.state);
      setCookieText("");
      setImportOpen(false);
      onToast("success", "ChatGPT session imported", "The account is available after its session and identity are verified.");
    } catch (error) {
      setImportError(message(error));
    } finally { setAction(""); }
  };

  const startLogin = async () => {
    setLoginError("");
    const result = await run("login", () => backend.proxyLoginGrok(grokName.trim()));
    if (result) setLogin(result);
  };

  const closeLogin = useCallback(async () => {
    if (action === "login" || action === "cancel-login") return;
    if (login) {
      const next = await run("cancel-login", () => backend.proxyCancelLogin(login.account_id, login.state));
      if (!next) return;
      setState(next);
      setLogin(null);
    }
    setGrokOpen(false);
    setLoginError("");
  }, [action, login, run]);

  const closeImport = useCallback(() => {
    if (action === "import") return;
    setImportOpen(false);
    setCookieText("");
  }, [action]);
  const closeRename = useCallback(() => { if (!busy) setRename(null); }, [busy]);
  const closeDelete = useCallback(() => { if (!busy) setPendingDelete(null); }, [busy]);

  const accounts = state?.accounts ?? [];
  const models = Array.from(new Set(accounts.filter((account) => account.enabled && ["ready", "busy", "cooldown"].includes(account.status)).flatMap((account) =>
    account.models.map((name) => name.startsWith(`${account.provider}/`) ? name : `${account.provider}/${name}`),
  ))).sort();
  const selectedModel = models.includes(model) ? model : models[0] || "";
  const unsupported = Boolean(state && !state.supported);

  return (
    <div className="animate-fade-in mx-auto w-full max-w-[720px] space-y-5 px-5 py-6">
      <div className="flex items-end justify-between gap-3 px-1">
        <div>
          <h1 className="display-title">API</h1>
          <p className="mt-2 text-[13px] text-[var(--color-secondary-label)]">One local endpoint for your ChatGPT and Grok account pools.</p>
        </div>
        <Button disabled={busy} onClick={() => update("refresh", backend.proxyState)}>
          <RefreshIcon className={cx("h-4 w-4", action === "refresh" && "animate-spin")} />Refresh
        </Button>
      </div>

      {loadError ? <Tip>{loadError}</Tip> : null}
      {state?.error ? <Tip>{state.error}</Tip> : null}
      {unsupported ? <Tip>Managed API engines require Windows x64. Account switching for your existing providers remains available.</Tip> : null}
      {!state ? <div className="liquid-glass h-40 animate-pulse rounded-[22px]" /> : <>
        <section className="liquid-glass rounded-[22px] p-5" aria-label="Local API gateway">
          <div className="relative z-[1] space-y-4">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <span className={cx("h-2.5 w-2.5 rounded-full", state.running ? "bg-emerald-500" : "bg-zinc-400")} />
                <h2 className="text-[15px] font-semibold">Gateway {state.running ? "running" : "stopped"}</h2>
              </div>
              <Button variant={state.running ? "secondary" : "primary"} disabled={busy || unsupported}
                onClick={() => update("gateway", state.running ? backend.proxyStop : backend.proxyStart)}>
                {action === "gateway" ? "Working…" : state.running ? "Stop API" : "Start API"}
              </Button>
            </div>
            <div className="flex items-center gap-2 rounded-[14px] bg-white/40 pl-3.5 pr-1.5 py-1.5">
              <code className="min-w-0 flex-1 select-text truncate text-[12px]" title={state.endpoint}>{state.endpoint}</code>
              <IconButton label="Copy API endpoint" disabled={busy} onClick={() => copy(state.endpoint, "Endpoint")}><CopyIcon className="h-4 w-4" /></IconButton>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-28"><Field label="Port"><TextInput type="number" min={1024} max={65535} value={port} disabled={state.running || busy}
                onChange={(event) => setPort(event.target.value)} className="h-9 text-[13px]" /></Field></div>
              <Button disabled={busy || state.running || !Number.isInteger(Number(port)) || Number(port) < 1024 || Number(port) > 65535 || Number(port) === state.port}
                onClick={() => update("port", () => backend.proxySetPort(Number(port)))}>Save port</Button>
              <span className="pb-2 text-[11px] text-[var(--color-tertiary-label)]">Stop the API before changing its port.</span>
            </div>
            <div>
              <p className="mb-1.5 text-[13px] font-medium text-[var(--color-secondary-label)]">Gateway API key</p>
              <div className="flex items-center gap-1 rounded-[14px] bg-white/40 pl-3.5 pr-1.5 py-1.5">
                <code className="min-w-0 flex-1 select-text truncate text-[12px]">{revealKey && key ? key : state.has_key ? "••••••••••••••••••••••••" : "Created when the gateway starts"}</code>
                <IconButton label={revealKey ? "Hide API key" : "Reveal API key"} disabled={busy || !state.has_key}
                  onClick={() => revealKey ? setRevealKey(false) : fetchKey(false)}>
                  {revealKey ? <EyeOffIcon className="h-4 w-4" /> : <EyeIcon className="h-4 w-4" />}
                </IconButton>
                <IconButton label="Copy API key" disabled={busy || !state.has_key} onClick={() => fetchKey(true)}><CopyIcon className="h-4 w-4" /></IconButton>
              </div>
            </div>
            <p className="text-[12px] text-[var(--color-secondary-label)]">Closing this window keeps the API in the system tray. Choose Exit in the tray to stop U-Pool and its engines.</p>
          </div>
        </section>

        <section className="liquid-glass rounded-[22px] p-5" aria-label="Managed runtimes">
          <div className="relative z-[1]">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="text-[15px] font-semibold">Managed components</h2>
              <Button disabled={busy || unsupported || state.runtimes.every((runtime) => runtime.installed)} onClick={() => update("install", backend.proxyInstall)}>
                {state.busy || action === "install" ? "Installing…" : "Install components"}
              </Button>
            </div>
            <ul className="divide-y divide-black/[0.05]">
              {state.runtimes.map((runtime) => <li key={runtime.id} className="py-2.5">
                <div className="flex items-center justify-between gap-2 text-[13px]">
                  <span className="font-medium">{RUNTIME_NAMES[runtime.id] || runtime.id} <span className="ml-1 text-[11px] font-normal text-[var(--color-tertiary-label)]">{runtime.version}</span></span>
                  <span className={runtime.installed ? "text-emerald-700" : runtime.error ? "text-red-600" : "text-[var(--color-secondary-label)]"}>
                    {RUNTIME_STATUS[runtime.status] || runtime.status || (runtime.installed ? "Installed" : "Not installed")}
                  </span>
                </div>
                {runtime.error ? <p className="mt-1 text-[12px] text-red-600">{runtime.error}</p> : null}
              </li>)}
            </ul>
          </div>
        </section>

        {(["chatgpt", "grok"] as const).map((provider) => {
          const pool = accounts.filter((account) => account.provider === provider);
          return <section key={provider} aria-label={`${provider} account pool`}>
            <div className="mb-3 flex items-center justify-between gap-3 px-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-[17px] font-semibold">{provider === "chatgpt" ? "ChatGPT" : "Grok"}</h2>
                {provider === "chatgpt" ? <span className="rounded-full bg-amber-100/80 px-2 py-0.5 text-[10px] font-semibold text-amber-900">Experimental coding</span> : null}
              </div>
              <Button disabled={busy || unsupported} onClick={() => {
                if (provider === "chatgpt") { setImportError(""); setImportOpen(true); }
                else { setLoginError(""); setGrokOpen(true); }
              }}><PlusIcon className="h-4 w-4" />{provider === "chatgpt" ? "Import cookies" : "Connect account"}</Button>
            </div>
            {pool.length === 0 ? <div className="liquid-glass rounded-[22px] px-5 py-8 text-center text-[13px] text-[var(--color-secondary-label)]">
              {provider === "chatgpt" ? "Import a ChatGPT cookie export to add your first account." : "Connect a Grok account through device sign-in."}
            </div> : <ul className="space-y-3">{pool.map((account) => <li key={account.id} className="liquid-glass rounded-[22px] p-4">
              <div className="relative z-[1]">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-[15px] font-semibold">{accountName(account)}</p>
                    {account.email && account.email !== account.name ? <p className="mt-0.5 truncate text-[12px] text-[var(--color-secondary-label)]">{account.email}</p> : null}
                    <p className="mt-1.5 text-[12px] text-[var(--color-secondary-label)]">{provider === "chatgpt" ? "Browser cookies" : "Device OAuth"} · <span className={cx(account.status === "ready" ? "text-emerald-700" : ["error", "reauth"].includes(account.status) ? "text-red-600" : "text-amber-800")}>{STATUS[account.status] || account.status}</span></p>
                  </div>
                  <Switch label={`Enable ${accountName(account)} in the API pool`} checked={account.enabled} disabled={busy}
                    onChange={(enabled) => update(account.id, () => backend.proxyUpdateAccount(account.id, { enabled }))} />
                </div>
                {account.error ? <p className="mt-2 text-[12px] text-red-600">{account.error}</p> : null}
                <p className="mt-3 break-words text-[12px] text-[var(--color-secondary-label)]">Models: {account.models.length ? account.models.join(", ") : "Awaiting verification"}</p>
                <p className="mt-1 text-[11px] text-[var(--color-tertiary-label)]">Quota: unknown · Last checked: {timestamp(account.last_checked)}</p>
                {account.cooldown_until > 0 ? <p className="mt-1 text-[11px] text-amber-800">Available after {timestamp(account.cooldown_until)}</p> : null}
                <div className="mt-3 flex items-center gap-1">
                  <Button disabled={busy || unsupported || account.status === "busy"} className="h-8 px-3 text-[12px]" onClick={() => update(account.id, () => backend.proxyRefreshAccount(account.id))}>
                    <RefreshIcon className={cx("h-3.5 w-3.5", action === account.id && "animate-spin")} />Check session
                  </Button>
                  {provider === "chatgpt" ? <Button disabled={busy || unsupported} className="h-8 px-3 text-[12px]"
                    onClick={() => run(account.id, () => backend.proxyOpenProfile(account.id))}>Open profile</Button> : null}
                  <span className="flex-1" />
                  <IconButton label={`Rename ${accountName(account)}`} disabled={busy} onClick={() => { setNewName(account.name); setRename(account); }}><PencilIcon className="h-4 w-4" /></IconButton>
                  <IconButton label={`Remove ${accountName(account)}`} danger disabled={busy || account.status === "busy"} onClick={() => setPendingDelete(account)}><TrashIcon className="h-4 w-4" /></IconButton>
                </div>
              </div>
            </li>)}</ul>}
          </section>;
        })}

        <section className="liquid-glass rounded-[22px] p-5" aria-label="Connect API to an app">
          <div className="relative z-[1] space-y-3">
            <h2 className="text-[15px] font-semibold">Connect an app</h2>
            <p className="text-[12px] text-[var(--color-secondary-label)]">Create a provider profile, then select Use in that app&apos;s tab to activate it.</p>
            {connectApp === "codex" && <p className="text-[12px] text-amber-500">Codex CLI 0.161.0 is currently incompatible with this API. Creating a profile does not enable coding for that version.</p>}
            <div className="grid grid-cols-[130px_minmax(0,1fr)] gap-3">
              <Field label="Application"><Select value={connectApp} onChange={(event) => setConnectApp(event.target.value as AppId)}>
                {Object.entries(APP_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </Select></Field>
              <Field label="Model"><Select value={selectedModel} disabled={!models.length} onChange={(event) => setModel(event.target.value)}>
                {!models.length ? <option value="">Verify an account first</option> : models.map((name) => <option key={name} value={name}>{name}</option>)}
              </Select></Field>
            </div>
            <Button variant="primary" disabled={busy || unsupported || !selectedModel} onClick={async () => {
              const result = await run("connect", () => backend.proxyConnect(connectApp, selectedModel));
              if (result) { onConnect(connectApp, result.state); onToast("success", `Provider created for ${APP_LABELS[connectApp]}`, "Select Use in the app's tab to activate it."); }
            }}>{action === "connect" ? "Creating…" : "Create provider profile"}</Button>
          </div>
        </section>

        <section className="liquid-glass rounded-[22px] p-5" aria-label="API usage">
          <div className="relative z-[1]">
            <h2 className="mb-3 text-[15px] font-semibold">API activity</h2>
            <dl className="grid grid-cols-3 gap-x-3 gap-y-4 text-[12px]">
              {[
                ["Requests", state.stats.requests.toLocaleString()], ["Errors", state.stats.errors.toLocaleString()],
                ["Average latency", state.stats.requests === 0 || state.stats.avg_latency_ms == null ? "Unknown" : `${Math.round(state.stats.avg_latency_ms).toLocaleString()} ms`],
                ["Input tokens", state.stats.input_tokens == null ? "Unknown" : state.stats.input_tokens.toLocaleString()],
                ["Output tokens", state.stats.output_tokens == null ? "Unknown" : state.stats.output_tokens.toLocaleString()],
              ].map(([label, value]) => <div key={label}><dt className="text-[var(--color-tertiary-label)]">{label}</dt><dd className="mt-1 text-[15px] font-semibold">{value}</dd></div>)}
            </dl>
            <p className="mt-4 text-[11px] text-[var(--color-tertiary-label)]">Token counts are reported only when available. Prompts and replies are not saved in activity records.</p>
          </div>
        </section>
      </>}

      {importOpen ? <Modal title="Import ChatGPT cookies" onClose={closeImport} footer={<>
        <Button disabled={action === "import"} onClick={closeImport}>Cancel</Button>
        <Button variant="primary" disabled={busy} onClick={importCookie}>{action === "import" ? "Verifying…" : "Import and verify"}</Button>
      </>}>
        <div className="space-y-3">
          <Field label="Account name (optional)"><TextInput value={importName} disabled={busy} onChange={(event) => setImportName(event.target.value)} placeholder="Personal account" /></Field>
          <Field label="Cookie export" hint="JSON, Netscape cookies.txt or a Cookie header. Export the complete session from chatgpt.com, including HttpOnly cookies.">
            <textarea value={cookieText} disabled={busy} onChange={(event) => { setCookieText(event.target.value); setImportError(""); }} spellCheck={false}
              placeholder={'[{"domain":".chatgpt.com","name":"…","value":"…"}]'}
              className="h-40 w-full resize-y rounded-[14px] bg-white/50 p-3 font-mono text-[11px] focus:outline-2 focus:outline-brand-400" />
          </Field>
          <Button disabled={busy} onClick={() => picker.current?.click()}><FileIcon className="h-4 w-4" />Choose file…</Button>
          <input ref={picker} type="file" accept=".json,.txt" className="hidden" onChange={async (event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (!file) return;
            try { setCookieText(await file.text()); setImportError(""); } catch (error) { setImportError(message(error)); }
          }} />
          {importError ? <p role="alert" className="text-[12px] text-red-600">{importError}</p> : null}
          <p className="text-[12px]">Each account uses its own managed browser profile. Reimporting the same account updates its session.</p>
        </div>
      </Modal> : null}

      {grokOpen ? <Modal title="Connect Grok account" onClose={closeLogin} footer={<>
        <Button disabled={action === "login" || action === "cancel-login"} onClick={closeLogin}>{login ? "Cancel sign-in" : "Close"}</Button>
        {!login ? <Button variant="primary" disabled={busy} onClick={startLogin}>{action === "login" ? "Starting…" : "Start sign-in"}</Button> : null}
      </>}>
        {login ? <div className="space-y-3">
          <p>Finish device sign-in in the browser that U-Pool opened.</p>
          {login.user_code ? <div className="flex items-center justify-between rounded-[14px] bg-white/50 px-4 py-2"><code className="select-text text-[20px] font-semibold tracking-widest">{login.user_code}</code>
            <IconButton label="Copy sign-in code" onClick={() => copy(login.user_code, "Sign-in code")}><CopyIcon className="h-4 w-4" /></IconButton></div> : null}
          {login.url ? <Button onClick={() => run("open-login", () => backend.openExternal(login.url))}>Open sign-in page</Button> : null}
          <p className="flex items-center gap-2 text-[12px]"><RefreshIcon className="h-3.5 w-3.5 animate-spin" />Waiting for sign-in…</p>
        </div> : <Field label="Account name (optional)"><TextInput value={grokName} disabled={busy} onChange={(event) => setGrokName(event.target.value)} placeholder="Personal account" /></Field>}
        {loginError ? <p className="mt-3 text-[12px] text-red-600" role="alert">{loginError}</p> : null}
      </Modal> : null}

      {rename ? <Modal title="Rename account" onClose={closeRename} footer={<>
        <Button disabled={busy} onClick={closeRename}>Cancel</Button><Button variant="primary" disabled={busy} onClick={async () => {
          const next = await run(rename.id, () => backend.proxyUpdateAccount(rename.id, { name: newName.trim() }));
          if (next) { setState(next); setRename(null); }
        }}>Save</Button>
      </>}><Field label="Name"><TextInput value={newName} onChange={(event) => setNewName(event.target.value)} /></Field></Modal> : null}

      {pendingDelete ? <Modal title={`Remove ${accountName(pendingDelete)}?`} onClose={closeDelete} footer={<>
        <Button disabled={busy} onClick={closeDelete}>Cancel</Button><Button variant="danger" disabled={busy} onClick={async () => {
          const next = await run(pendingDelete.id, () => backend.proxyDeleteAccount(pendingDelete.id));
          if (next) { setState(next); setPendingDelete(null); onToast("success", "Account removed"); }
        }}>Remove account</Button>
      </>}>This removes the account&apos;s saved credentials and managed profile from U-Pool. Wait for its current request to finish before removing it.</Modal> : null}
    </div>
  );
}
