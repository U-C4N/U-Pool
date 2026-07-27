export type AppId = "claude" | "codex";

export type AuthStyle = "auth_token" | "api_key";
export type WireApi = "responses" | "chat";

export interface AppInfo {
  id: AppId;
  label: string;
}

/** A provider as it comes back from list endpoints: no raw API key. */
export interface ProviderSummary {
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

export interface Bootstrap {
  version: string;
  platform: string;
  apps: AppInfo[];
  state: Record<AppId, AppState>;
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
}
