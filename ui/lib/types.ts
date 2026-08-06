export type AppId = "claude" | "claude_desktop" | "codex" | "hermes" | "opencode";

/**
 * What the tab bar keys on. Cursor is a tab, but it is not a provider app.
 *
 * Do not put "cursor" into `AppId` instead. Every `Record<AppId, …>` map in the
 * UI - brand marks, tab tints, URL hints, preset catalogues, the mock's per-app
 * tables - is exhaustive on purpose, and widening `AppId` would force each of
 * them to carry a Cursor entry describing a form Cursor never renders, a config
 * file it never writes and presets it cannot have. The union widens here so
 * those maps do not have to.
 */
export type TabId = AppId | "cursor";

export type AuthStyle = "auth_token" | "api_key";
export type WireApi = "responses" | "chat";

/** Hermes names the wire protocol per provider, in its own vocabulary. */
export type Transport =
  | "anthropic_messages"
  | "chat_completions"
  | "codex_responses"
  | "bedrock_converse";

/** OpenCode routes a provider through a Vercel AI SDK package. */
export type NpmPackage =
  | "@ai-sdk/anthropic"
  | "@ai-sdk/openai"
  | "@ai-sdk/openai-compatible"
  | "@ai-sdk/amazon-bedrock"
  | "@ai-sdk/google";

export interface AppInfo {
  id: AppId;
  label: string;
}

/**
 * Advanced toggles. Each one owns exactly one key in the target CLI's own
 * config file while it is on, and gives it back when it goes off.
 */
export interface ProviderToggles {
  /** Claude: permissions.defaultMode = "bypassPermissions". */
  bypass_permissions: boolean;
  /** Claude: permissions.skipDangerousModePermissionPrompt = true. */
  skip_bypass_prompt: boolean;
  /** Claude: permissions.defaultMode = "acceptEdits". */
  accept_edits: boolean;
  /** Claude: enableAllProjectMcpServers = true. */
  all_project_mcp: boolean;
  /** Codex: approval_policy = "never" + sandbox_mode = "danger-full-access". */
  bypass_approvals: boolean;
  /** Codex: web_search = "live". */
  web_search: boolean;
}

/** A provider as it comes back from list endpoints: no raw API key. */
export interface ProviderSummary extends ProviderToggles {
  id: string;
  app: AppId;
  name: string;
  note: string;
  website: string;
  base_url: string;
  model: string;
  auth_style: AuthStyle;
  small_fast_model: string;
  wire_api: WireApi;
  env_key: string;
  transport: Transport;
  npm: NpmPackage;
  extra: Record<string, string>;
  official: boolean;
  created_at: number;
  updated_at: number;
  api_key_masked: string;
  has_api_key: boolean;
  active: boolean;
}

/** The full record, API key included - only fetched to populate the edit form. */
export interface ProviderDetail extends Omit<ProviderSummary, "api_key_masked" | "has_api_key" | "active"> {
  api_key: string;
}

export interface AppState {
  current: string;
  providers: ProviderSummary[];
  files: string[];
}

/** Preferences that belong to U-Pool itself rather than to a provider. */
export interface AppSettings {
  /** What the OS actually reports, not just what was asked for. */
  launch_at_startup: boolean;
  autostart_supported: boolean;
  /** Registered, but switched off in Task Manager > Startup apps. */
  autostart_blocked: boolean;
  autostart_command: string;
  autostart_detail: string;
  /** Whether the periodic GitHub release check runs at all. */
  update_check_enabled: boolean;
  /** Off means no `<filename>.backup` copy is left beside a file U-Pool rewrites. */
  backup_enabled: boolean;
}

export type UpdatePhase =
  | "idle"
  | "checking"
  | "up_to_date"
  | "available"
  | "downloading"
  | "verifying"
  | "staging"
  | "relaunching"
  | "error";

/** The release GitHub is offering, as the backend read it. */
export interface ReleaseInfo {
  tag: string;
  version: string;
  notes: string;
  html_url: string;
  published_at: string;
  asset_name: string;
  asset_url: string;
  asset_size: number;
  asset_sha256: string;
  checksums_url: string;
  has_asset: boolean;
}

export interface UpdateStatus {
  phase: UpdatePhase;
  /** 0-100 while work is in flight, null otherwise. */
  percent: number | null;
  detail: string;
  release: ReleaseInfo | null;
  error: string;
  /** "frozen" for the packaged bundle, "source" for a checkout. */
  kind: string;
  can_install: boolean;
  /** Why an in-place install is not possible, in one sentence. */
  blocker: string;
  /** What the download could actually be checked against. */
  verified: string;
  skipped_version: string;
  last_check: number;
  current_version: string;
  /** Set on the launch right after a successful swap. */
  installed_from: string;
  install_failed: string;
  busy: boolean;
}

/** One target CLI as it is installed on this machine, or is not. */
export interface CliVersion {
  id: string;
  label: string;
  version: string;
  path: string;
  /** Found on disk. With an empty `version` this means broken, not missing. */
  found: boolean;
  error: string;
}

export interface CliVersions {
  tools: CliVersion[];
  busy: boolean;
  /** False until the first probe has answered; the header shows placeholders. */
  ready: boolean;
}

/** One thing `delete_sessions` would erase, with what it holds right now. */
export interface SessionEntry {
  path: string;
  exists: boolean;
  files: number;
  bytes: number;
}

export interface SessionSummary {
  app: AppId;
  entries: SessionEntry[];
  files: number;
  bytes: number;
}

export interface SessionPurge {
  app: AppId;
  deleted: number;
  freed: number;
  /** A transcript the running CLI still holds open lands here, not in a throw. */
  errors: string[];
  summary: SessionSummary;
}

export interface Bootstrap {
  version: string;
  platform: string;
  apps: AppInfo[];
  state: Record<AppId, AppState>;
  settings: AppSettings;
  update: UpdateStatus;
  clis: CliVersions;
}

export interface SwitchResult {
  state: AppState;
  files: string[];
  backups: string[];
  warnings: string[];
  /** Keys the switch dropped - the write is surgical, so everything else survived. */
  removed: string[];
  env_written: string[];
  env_removed: string[];
}

/**
 * The Windows environment is a second write target because the CLIs read
 * `HKCU\Environment` as well as their own config files - Codex's
 * `env_key = "codefast"` resolves nowhere else.
 */
export interface EnvInfo {
  supported: boolean;
  namespace: string;
  vars: { name: string; value_masked: string; owned: boolean }[];
}

export type HealthStatus = "ok" | "auth" | "warn" | "error" | "unreachable" | "skipped";

export interface HealthResult {
  provider_id: string;
  name: string;
  status: HealthStatus;
  latency_ms: number | null;
  http_status: number | null;
  message: string;
  reachable: boolean;
}

export interface AppPaths {
  home: string;
  config: string;
  backups: string;
  settings: string;
}

/** What the last refresh concluded. "unknown" is the honest answer before the first one. */
export type CursorStatus = "ok" | "expired" | "unknown";

/** Cursor meters a plan in dollars or in requests, so the unit travels with the numbers. */
export type CursorUsageUnit = "usd" | "requests" | "";

/**
 * One pooled Cursor account, as every endpoint hands it back.
 *
 * The session cookie is never in here. `api.py`'s rule about provider keys is
 * stronger for this one - the cookie is the whole account, not one endpoint's
 * access to it - so the UI gets `has_token` and nothing else.
 *
 * Every usage figure is nullable because it comes from undocumented cursor.com
 * endpoints that will change without notice; null renders as an em dash and must
 * never stop a switch.
 */
export interface CursorAccountSummary {
  id: string;
  /** The half of the cookie before `::`. This, not the email, is identity. */
  user_id: string;
  email: string;
  name: string;
  plan: string;
  plan_status: string;
  usage_used: number | null;
  usage_limit: number | null;
  usage_unit: CursorUsageUnit;
  usage_percent: number | null;
  status: CursorStatus;
  /** Milliseconds since the epoch; 0 means never refreshed. */
  last_checked: number;
  added_at: number;
  active: boolean;
  has_token: boolean;
  has_web_token: boolean;
  /**
   * Which kind of credential the stored token is, never the token itself. A
   * browser cookie is a `web` token: it fills the card but cannot sign the
   * desktop app in, so the card disables Use on it. A desktop sign-in is a
   * `session` token, which does.
   */
  token_kind: CursorTokenKind;
}

export type CursorTokenKind = "session" | "web" | "unknown";

export interface CursorState {
  accounts: CursorAccountSummary[];
  current: string;
  /** A refresh is in flight on a background thread; the UI polls while true. */
  busy: boolean;
  /** Cursor.exe is running right now, so a switch has to close it first. */
  running: boolean;
  /** This platform, with Cursor actually installed. */
  supported: boolean;
  db_path: string;
}

/**
 * A paste is counted, not itemised. A `cookies.txt` holds comments and every
 * other domain's cookies, so naming each skipped line is noise in a 200-line
 * paste - the toast says how many of each.
 */
export interface CursorAddResult {
  added: number;
  /** A re-pasted cookie for an account already in the pool refreshes its row. */
  refreshed: number;
  skipped: number;
  state: CursorState;
}

/** Files, backups and warnings in `SwitchResult`'s shape, so the toast is the same toast. */
export interface CursorUseResult {
  state: CursorState;
  files: string[];
  backups: string[];
  warnings: string[];
  closed_cursor: boolean;
  relaunched: boolean;
}
