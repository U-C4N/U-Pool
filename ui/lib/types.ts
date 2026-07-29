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
}

export interface Bootstrap {
  version: string;
  platform: string;
  apps: AppInfo[];
  state: Record<AppId, AppState>;
  settings: AppSettings;
}

export interface SwitchResult {
  state: AppState;
  files: string[];
  backups: string[];
  warnings: string[];
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
