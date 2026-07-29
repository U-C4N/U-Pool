"use client";

import { mockApi } from "./mock";
import type {
  AppId,
  AppPaths,
  AppSettings,
  AppState,
  Bootstrap,
  HealthResult,
  ProviderDetail,
  SwitchResult,
} from "./types";

/** Uniform response envelope produced by every Python endpoint. */
export interface Envelope<T> {
  ok: boolean;
  data?: T;
  error?: string;
}

export type RawApi = Record<string, (...args: unknown[]) => Promise<Envelope<unknown>>>;

declare global {
  interface Window {
    pywebview?: { api: RawApi };
  }
}

/** Thrown for anything the backend reported as a failure. */
export class BridgeError extends Error {}

let resolved: RawApi | null = null;

/**
 * pywebview injects its API asynchronously and announces it with a
 * `pywebviewready` event. In a plain browser that event never fires, so after a
 * short grace period we fall back to the in-memory mock and the UI stays usable
 * for `next dev`.
 */
async function api(): Promise<RawApi> {
  if (resolved) return resolved;
  if (typeof window === "undefined") throw new BridgeError("Bridge unavailable during SSR.");
  if (window.pywebview?.api) {
    resolved = window.pywebview.api;
    return resolved;
  }
  await new Promise<void>((done) => {
    const finish = () => done();
    window.addEventListener("pywebviewready", finish, { once: true });
    setTimeout(finish, 1000);
  });
  resolved = window.pywebview?.api ?? (mockApi as unknown as RawApi);
  return resolved;
}

export function isMockBridge(): boolean {
  return typeof window !== "undefined" && !window.pywebview?.api;
}

async function call<T>(method: string, ...args: unknown[]): Promise<T> {
  const target = await api();
  const fn = target[method];
  if (typeof fn !== "function") {
    throw new BridgeError(`Backend does not expose '${method}'.`);
  }
  const response = (await fn(...args)) as Envelope<T>;
  if (!response || typeof response !== "object") {
    throw new BridgeError(`Malformed response from '${method}'.`);
  }
  if (!response.ok) throw new BridgeError(response.error || "Something went wrong.");
  return response.data as T;
}

export const backend = {
  bootstrap: () => call<Bootstrap>("bootstrap"),
  listProviders: (app: AppId) => call<AppState>("list_providers", app),
  getProvider: (app: AppId, id: string) => call<ProviderDetail>("get_provider", app, id),
  saveProvider: (payload: Partial<ProviderDetail>) =>
    call<{ id: string; state: AppState }>("save_provider", payload),
  deleteProvider: (app: AppId, id: string) => call<AppState>("delete_provider", app, id),
  duplicateProvider: (app: AppId, id: string) =>
    call<{ id: string; state: AppState }>("duplicate_provider", app, id),
  reorderProviders: (app: AppId, ids: string[]) => call<AppState>("reorder_providers", app, ids),
  switchProvider: (app: AppId, id: string) => call<SwitchResult>("switch_provider", app, id),
  testProvider: (app: AppId, id: string) => call<HealthResult>("test_provider", app, id),
  testAll: (app: AppId) => call<HealthResult[]>("test_all", app),
  readLiveConfig: (app: AppId) =>
    call<{ path: string; exists: boolean; content: string }[]>("read_live_config", app),
  openPath: (target: string) => call<string>("open_path", target),
  openExternal: (url: string) => call<string>("open_external", url),
  appPaths: () => call<AppPaths>("app_paths"),
  getSettings: () => call<AppSettings>("get_settings"),
  setLaunchAtStartup: (enabled: boolean) =>
    call<AppSettings>("set_launch_at_startup", enabled),
  openStartupSettings: () => call<string>("open_startup_settings"),
};
