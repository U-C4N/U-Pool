"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Header } from "@/components/Header";
import { ProviderForm } from "@/components/ProviderForm";
import { ProviderList, type RowHandlers } from "@/components/ProviderList";
import { SettingsPanel } from "@/components/SettingsPanel";
import { ToastStack, useToasts } from "@/components/Toast";
import { Button, Modal } from "@/components/ui";
import { backend, isMockBridge } from "@/lib/bridge";
import type {
  AppId,
  AppInfo,
  AppPaths,
  AppSettings,
  AppState,
  HealthResult,
  ProviderDetail,
  ProviderSummary,
} from "@/lib/types";

type View = { mode: "list" } | { mode: "form"; provider: ProviderDetail | null };

const EMPTY_STATE: AppState = { current: "", providers: [], files: [] };

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export default function Page() {
  const [apps, setApps] = useState<AppInfo[]>([]);
  const [app, setApp] = useState<AppId>("claude");
  const [states, setStates] = useState<Partial<Record<AppId, AppState>>>({});
  const [health, setHealth] = useState<Record<string, HealthResult>>({});
  const [view, setView] = useState<View>({ mode: "list" });
  const [busyId, setBusyId] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<ProviderSummary | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);
  // Generation counter so a slow settings read cannot land on top of a newer write.
  const settingsRead = useRef(0);
  const [paths, setPaths] = useState<AppPaths | null>(null);
  const [meta, setMeta] = useState({ version: "", platform: "" });
  const [loadError, setLoadError] = useState<string | null>(null);
  // Resolved after mount only: the prerendered HTML must not depend on it.
  const [mockBridge, setMockBridge] = useState(false);
  const { toasts, push, dismiss } = useToasts();

  const state = states[app] ?? EMPTY_STATE;
  const appLabel = useMemo(
    () => apps.find((entry) => entry.id === app)?.label ?? app,
    [apps, app],
  );

  useEffect(() => {
    let cancelled = false;
    backend
      .bootstrap()
      .then((data) => {
        if (cancelled) return;
        setApps(data.apps);
        setStates(data.state);
        setApp(data.apps[0]?.id ?? "claude");
        setMeta({ version: data.version, platform: data.platform });
        setSettings(data.settings);
        // Checked only once the bridge has resolved, otherwise pywebview's
        // late API injection would look like a missing backend.
        setMockBridge(isMockBridge());
      })
      .catch((error) => !cancelled && setLoadError(message(error)));
    backend.appPaths().then(setPaths).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const apply = useCallback((target: AppId, next: AppState) => {
    setStates((current) => ({ ...current, [target]: next }));
  }, []);

  const run = useCallback(
    async <T,>(id: string | null, action: () => Promise<T>): Promise<T | null> => {
      setBusyId(id);
      try {
        return await action();
      } catch (error) {
        push("error", message(error));
        return null;
      } finally {
        setBusyId(null);
      }
    },
    [push],
  );

  const handleSwitch = useCallback(
    async (provider: ProviderSummary) => {
      const result = await run(provider.id, () => backend.switchProvider(app, provider.id));
      if (!result) return;
      apply(app, result.state);
      if (result.warnings.length > 0) {
        push("error", `Switched to ${provider.name}`, result.warnings.join(" "));
      } else {
        push("success", `${provider.name} is now in use`, result.files.join("  •  "));
      }
    },
    [app, apply, push, run],
  );

  const handleTest = useCallback(
    async (provider: ProviderSummary) => {
      const result = await run(provider.id, () => backend.testProvider(app, provider.id));
      if (!result) return;
      setHealth((current) => ({ ...current, [result.provider_id]: result }));
      push(result.reachable ? "success" : "error", `${provider.name} — ${result.message}`);
    },
    [app, push, run],
  );

  const handleTestAll = useCallback(async () => {
    setTesting(true);
    try {
      const results = await backend.testAll(app);
      setHealth((current) => {
        const next = { ...current };
        for (const result of results) next[result.provider_id] = result;
        return next;
      });
      const probed = results.filter((result) => result.status !== "skipped");
      const reachable = probed.filter((result) => result.reachable).length;
      push(
        reachable === probed.length ? "success" : "error",
        `${reachable}/${probed.length} providers reachable`,
      );
    } catch (error) {
      push("error", message(error));
    } finally {
      setTesting(false);
    }
  }, [app, push]);

  const handleEdit = useCallback(
    async (provider: ProviderSummary) => {
      const detail = await run(provider.id, () => backend.getProvider(app, provider.id));
      if (detail) setView({ mode: "form", provider: detail });
    },
    [app, run],
  );

  const handleDuplicate = useCallback(
    async (provider: ProviderSummary) => {
      const result = await run(provider.id, () => backend.duplicateProvider(app, provider.id));
      if (!result) return;
      apply(app, result.state);
      push("success", `Copied ${provider.name}`);
    },
    [app, apply, push, run],
  );

  const confirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    const provider = pendingDelete;
    setPendingDelete(null);
    const next = await run(provider.id, () => backend.deleteProvider(app, provider.id));
    if (!next) return;
    apply(app, next);
    push("success", `Deleted ${provider.name}`);
  }, [app, apply, pendingDelete, push, run]);

  const handleReorder = useCallback(
    async (ids: string[]) => {
      const next = await run(null, () => backend.reorderProviders(app, ids));
      if (next) apply(app, next);
    },
    [app, apply, run],
  );

  const handleSave = useCallback(
    async (draft: Partial<ProviderDetail>) => {
      setSaving(true);
      try {
        const result = await backend.saveProvider({ ...draft, app });
        apply(app, result.state);
        setView({ mode: "list" });
        push("success", draft.id ? `Saved ${draft.name}` : `Added ${draft.name}`);
      } catch (error) {
        push("error", message(error));
      } finally {
        setSaving(false);
      }
    },
    [app, apply, push],
  );

  const openPath = useCallback(
    (target: string) => {
      backend.openPath(target).catch((error) => push("error", message(error)));
    },
    [push],
  );

  const handleToggleStartup = useCallback(
    async (enabled: boolean) => {
      setSavingSettings(true);
      // Writing wins over any read still in flight, whatever order they resolve in.
      settingsRead.current += 1;
      try {
        const next = await backend.setLaunchAtStartup(enabled);
        setSettings(next);
        if (next.autostart_blocked) {
          push("error", "Windows is ignoring the startup entry", next.autostart_detail);
        } else if (next.launch_at_startup === enabled) {
          push("success", enabled ? "U-Pool will open when you sign in" : "Startup entry removed");
        } else {
          push("error", "The startup entry did not change", next.autostart_detail);
        }
      } catch (error) {
        push("error", message(error));
      } finally {
        setSavingSettings(false);
      }
    },
    [push],
  );

  const openStartupSettings = useCallback(() => {
    backend.openStartupSettings().catch((error) => push("error", message(error)));
  }, [push]);

  const openSettings = useCallback(() => {
    setSettingsOpen(true);
    // Windows can have switched the startup entry off since the last look.
    const token = (settingsRead.current += 1);
    backend
      .getSettings()
      .then((next) => token === settingsRead.current && setSettings(next))
      .catch(() => undefined);
  }, []);

  const handlers: RowHandlers = {
    onSwitch: handleSwitch,
    onEdit: handleEdit,
    onDuplicate: handleDuplicate,
    onTest: handleTest,
    onDelete: setPendingDelete,
    onOpenWebsite: (url) => backend.openExternal(url).catch((error) => push("error", message(error))),
  };

  if (loadError) {
    return (
      <main className="grid min-h-screen place-items-center p-10 text-center">
        <div>
          <p className="text-sm font-semibold text-red-600">U-Pool could not start</p>
          <p className="mt-2 max-w-md text-sm text-zinc-500">{loadError}</p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen">
      <Header
        apps={apps}
        activeApp={app}
        version={meta.version}
        onSelectApp={(next) => {
          setApp(next);
          setView({ mode: "list" });
        }}
        onAdd={() => setView({ mode: "form", provider: null })}
        onTestAll={handleTestAll}
        onOpenFolder={() => state.files[0] && openPath(state.files[0])}
        onOpenSettings={openSettings}
        testing={testing}
      />

      {mockBridge ? (
        <p className="mx-auto mt-2 max-w-[760px] px-4">
          <span className="liquid-pill block rounded-full px-3 py-1.5 text-center text-[12px] font-medium text-amber-900/80">
            Browser preview — in-memory mock, no config files are touched.
          </span>
        </p>
      ) : null}

      {view.mode === "list" ? (
        <div className="animate-fade-in mx-auto w-full max-w-[720px] px-5 py-6">
          <div className="mb-5 px-1">
            <h1 className="display-title text-[var(--color-label)]">Providers</h1>
            <p className="mt-2 text-[13px] leading-snug tracking-[-0.01em] text-[var(--color-secondary-label)]">
              Select a row to make it active for {appLabel}.
            </p>
          </div>
          <ProviderList
            providers={state.providers}
            health={health}
            busyId={busyId}
            handlers={handlers}
            onReorder={handleReorder}
          />
        </div>
      ) : (
        <ProviderForm
          app={app}
          appLabel={appLabel}
          initial={view.provider}
          saving={saving}
          onCancel={() => setView({ mode: "list" })}
          onSave={handleSave}
        />
      )}

      {pendingDelete ? (
        <Modal
          title={`Delete ${pendingDelete.name}?`}
          onClose={() => setPendingDelete(null)}
          footer={
            <>
              <Button onClick={() => setPendingDelete(null)}>Cancel</Button>
              <Button variant="danger" onClick={confirmDelete}>
                Delete
              </Button>
            </>
          }
        >
          This removes the provider from U-Pool. Your live {appLabel} config is left exactly as it is.
        </Modal>
      ) : null}

      {settingsOpen ? (
        <SettingsPanel
          version={meta.version}
          platform={meta.platform}
          paths={paths}
          liveFiles={state.files}
          settings={settings}
          savingSettings={savingSettings}
          onOpen={openPath}
          onToggleStartup={handleToggleStartup}
          onOpenStartupSettings={openStartupSettings}
          onClose={() => setSettingsOpen(false)}
        />
      ) : null}

      <ToastStack toasts={toasts} onDismiss={dismiss} />
    </main>
  );
}
