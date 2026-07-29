"use client";

/**
 * In-memory stand-in for the Python bridge.
 *
 * Only used when the UI runs in a normal browser (`npm run dev`), so the
 * layout can be built and reviewed without launching the desktop shell. It
 * mirrors the envelope shape of the real endpoints, nothing more.
 */
import type {
  AppId,
  AppSettings,
  AppState,
  Bootstrap,
  HealthResult,
  ProviderDetail,
} from "./types";

type Envelope<T> = { ok: boolean; data?: T; error?: string };

const ok = <T,>(data: T): Promise<Envelope<T>> => Promise.resolve({ ok: true, data });
const fail = (error: string): Promise<Envelope<never>> => Promise.resolve({ ok: false, error });

const OFFICIAL_SITE: Record<AppId, string> = {
  claude: "https://www.anthropic.com/claude-code",
  codex: "https://developers.openai.com/codex",
};

function seed(app: AppId, name: string, base: string, official = false): ProviderDetail {
  return {
    id: `${app}-${name.toLowerCase().replace(/\W+/g, "-")}`,
    app,
    name,
    note: "",
    website: official ? OFFICIAL_SITE[app] : base,
    base_url: official ? "" : base,
    api_key: official ? "" : "sk-demo-0000-0000",
    model: "",
    auth_style: "auth_token",
    small_fast_model: "",
    wire_api: "responses",
    env_key: "OPENAI_API_KEY",
    extra: {},
    bypass_permissions: false,
    skip_bypass_prompt: false,
    accept_edits: false,
    all_project_mcp: false,
    bypass_approvals: false,
    web_search: false,
    official,
    created_at: Date.now(),
    updated_at: Date.now(),
  };
}

const mockSettings: AppSettings = {
  launch_at_startup: false,
  autostart_supported: false,
  autostart_blocked: false,
  autostart_command: "",
  autostart_detail: "Browser preview - the real switch needs the desktop app.",
};

const db: Record<AppId, { current: string; providers: ProviderDetail[] }> = {
  claude: {
    current: "claude-default",
    providers: [
      seed("claude", "Claude Official", "", true),
      { ...seed("claude", "default", "https://api.kimi.com/coding"), id: "claude-default" },
    ],
  },
  codex: {
    current: "codex-openai-official",
    providers: [seed("codex", "OpenAI Official", "", true)],
  },
};

function state(app: AppId): AppState {
  const slot = db[app];
  return {
    current: slot.current,
    files:
      app === "claude"
        ? ["~/.claude/settings.json"]
        : ["~/.codex/config.toml", "~/.codex/auth.json"],
    providers: slot.providers.map((p) => {
      const { api_key, ...rest } = p;
      return {
        ...rest,
        api_key_masked: api_key ? `${api_key.slice(0, 4)}******${api_key.slice(-4)}` : "",
        has_api_key: Boolean(api_key),
        active: p.id === slot.current,
      };
    }),
  };
}

const find = (app: AppId, id: string) => db[app].providers.find((p) => p.id === id);

export const mockApi = {
  bootstrap: () =>
    ok<Bootstrap>({
      version: "0.4.0-mock",
      platform: "browser",
      apps: [
        { id: "claude", label: "Claude Code" },
        { id: "codex", label: "Codex" },
      ],
      state: { claude: state("claude"), codex: state("codex") },
      settings: mockSettings,
    }),
  list_providers: (app: AppId) => ok(state(app)),
  get_provider: (app: AppId, id: string) => {
    const provider = find(app, id);
    return provider ? ok(provider) : fail("That provider no longer exists.");
  },
  save_provider: (payload: ProviderDetail) => {
    const app = payload.app;
    const existing = payload.id ? find(app, payload.id) : undefined;
    if (existing) {
      Object.assign(existing, payload, { api_key: payload.api_key || existing.api_key });
      return ok({ id: existing.id, state: state(app) });
    }
    const created = { ...seed(app, payload.name, payload.base_url), ...payload, id: `m-${Date.now()}` };
    db[app].providers.push(created);
    return ok({ id: created.id, state: state(app) });
  },
  delete_provider: (app: AppId, id: string) => {
    if (db[app].current === id) return fail("This provider is in use. Switch to another one first.");
    db[app].providers = db[app].providers.filter((p) => p.id !== id);
    return ok(state(app));
  },
  duplicate_provider: (app: AppId, id: string) => {
    const source = find(app, id);
    if (!source) return fail("That provider no longer exists.");
    const copy = { ...source, id: `m-${Date.now()}`, name: `${source.name} copy`, official: false };
    db[app].providers.push(copy);
    return ok({ id: copy.id, state: state(app) });
  },
  reorder_providers: (app: AppId, ids: string[]) => {
    db[app].providers = ids
      .map((id) => find(app, id))
      .filter((p): p is ProviderDetail => Boolean(p));
    return ok(state(app));
  },
  switch_provider: (app: AppId, id: string) => {
    if (!find(app, id)) return fail("That provider no longer exists.");
    db[app].current = id;
    return ok({ state: state(app), files: state(app).files, backups: [], warnings: [] });
  },
  test_provider: (app: AppId, id: string): Promise<Envelope<HealthResult>> => {
    const provider = find(app, id);
    if (!provider) return fail("That provider no longer exists.");
    const latency = 120 + Math.floor(Math.random() * 700);
    return ok(
      provider.official
        ? {
            provider_id: id,
            name: provider.name,
            status: "skipped",
            latency_ms: null,
            http_status: null,
            message: "Official login - nothing to probe.",
            reachable: false,
          }
        : {
            provider_id: id,
            name: provider.name,
            status: "ok",
            latency_ms: latency,
            http_status: 200,
            message: `Reachable (${latency}ms)`,
            reachable: true,
          },
    );
  },
  test_all: async (app: AppId) => {
    const results = await Promise.all(
      db[app].providers.map(async (p) => (await mockApi.test_provider(app, p.id)).data as HealthResult),
    );
    return ok(results);
  },
  read_live_config: (app: AppId) =>
    ok(state(app).files.map((path) => ({ path, exists: true, content: "// mock bridge\n" }))),
  open_path: (target: string) => ok(target),
  open_external: (url: string) => {
    window.open(url, "_blank", "noopener");
    return ok(url);
  },
  app_paths: () =>
    ok({
      home: "~/.u-pool",
      config: "~/.u-pool/config.json",
      backups: "~/.u-pool/backups",
      settings: "~/.u-pool/settings.json",
    }),
  // A fresh object per call, like the Python endpoint: React skips a state update
  // that hands it back the same reference.
  get_settings: () => ok<AppSettings>({ ...mockSettings }),
  set_launch_at_startup: (enabled: boolean) => {
    // The real endpoint refuses outside Windows; the mock says the same thing.
    if (!mockSettings.autostart_supported) return fail("Launching at sign-in is wired up for Windows only.");
    mockSettings.launch_at_startup = enabled;
    return ok<AppSettings>({ ...mockSettings });
  },
  open_startup_settings: () => fail("Launching at sign-in is wired up for Windows only."),
};
