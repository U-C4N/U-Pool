export type AppId = "claude" | "codex";

export type AuthStyle = "auth_token" | "api_key";
export type WireApi = "responses" | "chat";

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

export interface Bootstrap {
  version: string;
  platform: string;
  apps: AppInfo[];
  state: Record<AppId, AppState>;
  settings: AppSettings;
  update: UpdateStatus;
}

export interface SwitchResult {
  state: AppState;
  files: string[];
  backups: string[];
  warnings: string[];
  /** Keys that were in the live file and are not any more - a switch rewrites it whole. */
  removed: string[];
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
