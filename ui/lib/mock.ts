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
  CliVersions,
  EnvInfo,
  HealthResult,
  ProviderDetail,
  ReleaseInfo,
  SessionPurge,
  SessionSummary,
  SwitchResult,
  UpdateStatus,
} from "./types";

type Envelope<T> = { ok: boolean; data?: T; error?: string };

const ok = <T,>(data: T): Promise<Envelope<T>> => Promise.resolve({ ok: true, data });
const fail = (error: string): Promise<Envelope<never>> => Promise.resolve({ ok: false, error });

const OFFICIAL_SITE: Record<AppId, string> = {
  claude: "https://www.anthropic.com/claude-code",
  claude_desktop: "https://claude.ai/download",
  codex: "https://developers.openai.com/codex",
  hermes: "https://github.com/NousResearch/hermes",
  opencode: "https://opencode.ai",
};

/** Claude Desktop is preview-only in 0.7.0, so its adapter reports no live files. */
const FILES: Record<AppId, string[]> = {
  claude: ["~/.claude/settings.json"],
  claude_desktop: [],
  codex: ["~/.codex/config.toml", "~/.codex/auth.json"],
  hermes: ["%LOCALAPPDATA%/hermes/config.yaml"],
  opencode: ["~/.config/opencode/opencode.json"],
};

type SwitchOutcome = Omit<SwitchResult, "state" | "files">;

/** One canned outcome per app, so the switch toast has a sample of every kind of line. */
const SWITCH_OUTCOME: Record<AppId, SwitchOutcome> = {
  claude: {
    backups: ["~/.claude/settings.json.backup"],
    warnings: [],
    removed: ["env.API_TIMEOUT_MS", "permissions.defaultMode"],
    env_written: ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"],
    env_removed: ["ANTHROPIC_API_KEY"],
  },
  claude_desktop: {
    backups: [],
    warnings: [
      "Claude Desktop is preview-only in 0.7.0 — your choice was recorded, but no configuration was written.",
    ],
    removed: [],
    env_written: [],
    env_removed: [],
  },
  codex: {
    backups: ["~/.codex/config.toml.backup", "~/.codex/auth.json.backup"],
    warnings: [],
    removed: ["model_providers.yunwu"],
    env_written: ["codefast", "OPENAI_BASE_URL"],
    env_removed: [],
  },
  // Both write files only - neither reads its key out of the environment, so the
  // env lines stay empty here the way the real adapters leave them.
  hermes: {
    backups: ["%LOCALAPPDATA%/hermes/config.yaml.backup"],
    warnings: [],
    removed: ["providers.yunwu"],
    env_written: [],
    env_removed: [],
  },
  opencode: {
    backups: ["~/.config/opencode/opencode.json.backup"],
    warnings: [],
    removed: ["provider.yunwu"],
    env_written: [],
    env_removed: [],
  },
};

const ANTHROPIC_ENV: EnvInfo = {
  supported: true,
  namespace: "anthropic",
  vars: [
    { name: "ANTHROPIC_BASE_URL", value_masked: "https://api.kimi.com/coding", owned: true },
    { name: "ANTHROPIC_AUTH_TOKEN", value_masked: "sk-d******0000", owned: true },
  ],
};

// Claude Code and Claude Desktop share one set of variable names, but only Claude
// Code's adapter claims a namespace - the desktop one is preview-only and writes
// nothing. So the real endpoint answers an empty namespace here, and the mock says
// the same rather than showing a section the desktop app never renders.
const NO_ENV: EnvInfo = { supported: false, namespace: "", vars: [] };

const MOCK_ENV: Record<AppId, EnvInfo> = {
  claude: ANTHROPIC_ENV,
  claude_desktop: NO_ENV,
  // Hermes reads its key from config.yaml and OpenCode from opencode.json, so
  // neither claims a namespace and the panel says so instead of listing nothing.
  hermes: NO_ENV,
  opencode: NO_ENV,
  codex: {
    supported: true,
    namespace: "openai",
    vars: [
      { name: "codefast", value_masked: "sk-d******0000", owned: true },
      { name: "OPENAI_BASE_URL", value_masked: "https://api.openai.com/v1", owned: true },
      // Set by hand before U-Pool existed: listed, never deleted.
      { name: "OPENAI_API_KEY", value_masked: "sk-p******9f21", owned: false },
    ],
  },
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
    transport: "anthropic_messages",
    npm: "@ai-sdk/anthropic",
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
  update_check_enabled: true,
  backup_enabled: true,
};

const mockRelease: ReleaseInfo = {
  tag: "v0.9.0",
  version: "0.9.0",
  notes: "Pretend release notes for the browser preview.",
  html_url: "https://github.com/U-C4N/U-Pool/releases/latest",
  published_at: "2026-08-01T00:00:00Z",
  asset_name: "U-Pool-0.9.0-win64.zip",
  asset_url: "https://github.com/U-C4N/U-Pool/releases/download/v0.9.0/U-Pool-0.9.0-win64.zip",
  asset_size: 48 * 1024 * 1024,
  asset_sha256: "0".repeat(64),
  checksums_url: "",
  has_asset: true,
};

/**
 * Enough of the update state machine to design against: an available update,
 * then a download that ticks along whenever the UI polls.
 */
const mockUpdate: UpdateStatus = {
  phase: "available",
  percent: null,
  detail: "",
  release: mockRelease,
  error: "",
  kind: "source",
  can_install: false,
  blocker: "Browser preview - installing an update needs the desktop app.",
  verified: "",
  skipped_version: "",
  last_check: Math.floor(Date.now() / 1000),
  current_version: "0.7.0-mock",
  installed_from: "",
  install_failed: "",
  busy: false,
};

const db: Record<AppId, { current: string; providers: ProviderDetail[] }> = {
  claude: {
    current: "claude-default",
    providers: [
      seed("claude", "Claude Official", "", true),
      { ...seed("claude", "default", "https://api.kimi.com/coding"), id: "claude-default" },
    ],
  },
  // A saved-but-inactive custom entry, because the preview notice is only worth
  // looking at with something under it that U-Pool is not writing yet.
  claude_desktop: {
    current: "claude_desktop-claude-official",
    providers: [
      seed("claude_desktop", "Claude Official", "", true),
      seed("claude_desktop", "Kimi relay", "https://api.kimi.com/coding"),
    ],
  },
  codex: {
    current: "codex-openai-official",
    providers: [seed("codex", "OpenAI Official", "", true)],
  },
  hermes: {
    current: "hermes-hermes-default",
    providers: [
      seed("hermes", "Hermes Default", "", true),
      {
        ...seed("hermes", "Kimi For Coding", "https://api.kimi.com/coding"),
        model: "kimi-for-coding",
      },
    ],
  },
  opencode: {
    current: "opencode-opencode-default",
    providers: [
      seed("opencode", "OpenCode Default", "", true),
      {
        ...seed("opencode", "OpenCode Go", "https://opencode.ai/zen/go/v1"),
        model: "deepseek-v4-flash",
        npm: "@ai-sdk/openai-compatible",
      },
    ],
  },
};

const mockClis: CliVersions = {
  tools: [
    {
      id: "claude",
      label: "Claude Code",
      version: "2.1.4",
      path: "%APPDATA%/npm/claude.cmd",
      found: true,
      error: "",
    },
    // Left unfound on purpose: the placeholder is the state the header is hardest
    // to get right, so the preview should always be showing one of them.
    {
      id: "codex",
      label: "Codex",
      version: "",
      path: "",
      found: false,
      error: "Not installed, or not on this app's PATH.",
    },
  ],
  busy: false,
  ready: true,
};

/** Only the two apps whose transcript locations the backend actually knows. */
const mockSessions: Record<string, SessionSummary> = {
  claude: {
    app: "claude",
    entries: [
      { path: "~/.claude/projects", exists: true, files: 936, bytes: 240_766_373 },
      { path: "~/.claude/sessions", exists: true, files: 2, bytes: 706 },
      { path: "~/.claude/history.jsonl", exists: true, files: 1, bytes: 22_643 },
    ],
    files: 939,
    bytes: 240_789_722,
  },
  codex: {
    app: "codex",
    entries: [
      { path: "~/.codex/sessions", exists: true, files: 29, bytes: 19_058_523 },
      { path: "~/.codex/archived_sessions", exists: true, files: 24, bytes: 9_590_213 },
      { path: "~/.codex/history.jsonl", exists: true, files: 1, bytes: 22_643 },
      { path: "~/.codex/session_index.jsonl", exists: false, files: 0, bytes: 0 },
    ],
    files: 54,
    bytes: 28_671_379,
  },
};

function emptySummary(app: string): SessionSummary {
  const previous = mockSessions[app];
  return {
    app: app as AppId,
    entries: (previous?.entries ?? []).map((entry) => ({ ...entry, files: 0, bytes: 0 })),
    files: 0,
    bytes: 0,
  };
}

function state(app: AppId): AppState {
  const slot = db[app];
  return {
    current: slot.current,
    files: [...FILES[app]],
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
      version: "0.7.0-mock",
      platform: "browser",
      apps: [
        { id: "claude", label: "Claude Code" },
        { id: "claude_desktop", label: "Claude Desktop" },
        { id: "codex", label: "Codex" },
        { id: "hermes", label: "Hermes" },
        { id: "opencode", label: "OpenCode" },
      ],
      state: {
        claude: state("claude"),
        claude_desktop: state("claude_desktop"),
        codex: state("codex"),
        hermes: state("hermes"),
        opencode: state("opencode"),
      },
      settings: mockSettings,
      update: { ...mockUpdate },
      clis: { ...mockClis, tools: mockClis.tools.map((tool) => ({ ...tool })) },
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
    const next = state(app);
    return ok<SwitchResult>({ state: next, files: next.files, ...SWITCH_OUTCOME[app] });
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
  set_backup_enabled: (enabled: boolean) => {
    mockSettings.backup_enabled = enabled;
    return ok<AppSettings>({ ...mockSettings });
  },
  open_startup_settings: () => fail("Launching at sign-in is wired up for Windows only."),
  environment: (app: AppId) => ok<EnvInfo>({ ...MOCK_ENV[app], vars: [...MOCK_ENV[app].vars] }),
  open_env_settings: () => fail("The Windows environment editor needs the desktop app."),
  update_status: () => {
    // Advance the fake download a step per poll, so the bar actually moves.
    if (mockUpdate.phase === "downloading") {
      const next = (mockUpdate.percent ?? 0) + 8;
      if (next >= 80) {
        Object.assign(mockUpdate, {
          phase: "relaunching",
          percent: 100,
          detail: "Restarting into 0.9.0…",
          busy: false,
        });
      } else {
        Object.assign(mockUpdate, { percent: next, detail: `${next * 6} MB of 480 MB` });
      }
    }
    return ok<UpdateStatus>({ ...mockUpdate });
  },
  check_updates: () => {
    Object.assign(mockUpdate, { phase: "available", error: "", detail: "", busy: false });
    return ok<UpdateStatus>({ ...mockUpdate });
  },
  install_update: () => {
    if (!mockUpdate.can_install) return fail(mockUpdate.blocker);
    Object.assign(mockUpdate, { phase: "downloading", percent: 0, busy: true });
    return ok<UpdateStatus>({ ...mockUpdate });
  },
  skip_update: (version: string) => {
    mockUpdate.skipped_version = version;
    return ok<UpdateStatus>({ ...mockUpdate });
  },
  set_update_checks: () => ok<UpdateStatus>({ ...mockUpdate }),
  cli_versions: () => ok<CliVersions>({ ...mockClis, tools: mockClis.tools.map((t) => ({ ...t })) }),
  refresh_cli_versions: () =>
    ok<CliVersions>({ ...mockClis, tools: mockClis.tools.map((t) => ({ ...t })) }),
  session_summary: (app: string) => {
    const summary = mockSessions[app];
    return summary ? ok<SessionSummary>(summary) : fail(`U-Pool does not track sessions for '${app}'.`);
  },
  delete_sessions: (app: string) => {
    const summary = mockSessions[app];
    if (!summary) return fail(`U-Pool does not track sessions for '${app}'.`);
    const deleted = summary.files;
    const freed = summary.bytes;
    mockSessions[app] = emptySummary(app);
    return ok<SessionPurge>({
      app: app as AppId,
      deleted,
      freed,
      errors: [],
      summary: mockSessions[app],
    });
  },
  quit: () => ok(true),
};
