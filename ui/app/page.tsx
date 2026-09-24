"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CursorPool } from "@/components/CursorPool";
import { Header } from "@/components/Header";
import { ProviderForm } from "@/components/ProviderForm";
import { ProviderList, type RowHandlers } from "@/components/ProviderList";
import { SettingsPanel } from "@/components/SettingsPanel";
import { ToastStack, useToasts } from "@/components/Toast";
import { Button, Modal } from "@/components/ui";
import { UsagePanel } from "@/components/UsagePanel";
import { backend, isMockBridge } from "@/lib/bridge";
import type {
  AppId,
  AppInfo,
  AppPaths,
  AppSettings,
  AppState,
  CliVersions,
  EnvInfo,
  HealthResult,
  ProviderDetail,
  ProviderSummary,
  SessionSummary,
  TabId,
  UpdateStatus,
} from "@/lib/types";

type View = { mode: "list" } | { mode: "form"; provider: ProviderDetail | null };

const EMPTY_STATE: AppState = { current: "", providers: [], files: [] };

/**
 * The apps whose transcripts U-Pool knows how to find. Hermes and OpenCode have
 * tabs but are absent on purpose - the backend refuses them, and a delete button
 * that guesses at a path is the one kind of guess this feature must not make.
 */
const SESSION_APPS: Record<string, string> = {
  claude: "Claude Code",
  codex: "Codex",
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The toast keeps one line of detail, so a long list of names becomes a count. */
function shortList(names: string[], limit = 3): string {
  return names.length <= limit
    ? names.join(", ")
    : `${names.slice(0, limit).join(", ")} +${names.length - limit} more`;
}

export default function Page() {
  const [apps, setApps] = useState<AppInfo[]>([]);
  // Two pieces of state for one row of tabs, because Cursor is a tab and not an
  // app: `app` stays a real `AppId` while the Cursor tab is open, so the provider
  // calls, the per-app state map and the Settings panel keep working on the app
  // the user was last looking at rather than on something that cannot answer.
  const [tab, setTab] = useState<TabId>("claude");
  const [app, setApp] = useState<AppId>("claude");
  const [states, setStates] = useState<Partial<Record<AppId, AppState>>>({});
  const [health, setHealth] = useState<Record<string, HealthResult>>({});
  const [view, setView] = useState<View>({ mode: "list" });
  // Counts presses of +, so a second one on an already-open add form starts over.
  // The form key alone cannot tell two visits to a blank form apart.
  const [addSeq, setAddSeq] = useState(0);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<ProviderSummary | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [env, setEnv] = useState<EnvInfo | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);
  // Generation counter so a slow settings read cannot land on top of a newer write.
  const settingsRead = useRef(0);
  const [update, setUpdate] = useState<UpdateStatus | null>(null);
  const [clis, setClis] = useState<CliVersions | null>(null);
  const [sessions, setSessions] = useState<Record<string, SessionSummary>>({});
  const [deletingSessions, setDeletingSessions] = useState("");
  const [sessionErrors, setSessionErrors] = useState<Record<string, string[]>>({});
  const [pendingPurge, setPendingPurge] = useState<string | null>(null);
  const [paths, setPaths] = useState<AppPaths | null>(null);
  // Where Cursor keeps the database a switch writes. Only the backend knows it,
  // and only the Settings panel shows it, so it is read when that panel opens.
  const [cursorDb, setCursorDb] = useState("");
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
        setTab(data.apps[0]?.id ?? "claude");
        setMeta({ version: data.version, platform: data.platform });
        setSettings(data.settings);
        setUpdate(data.update);
        setClis(data.clis);
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

  const selectTab = useCallback((next: TabId) => {
    setTab(next);
    // Cursor and Usage both leave `app` where it was: neither is a provider app,
    // so there is nothing for `app` to point at while either tab is active.
    if (next !== "cursor" && next !== "usage") setApp(next);
    setView({ mode: "list" });
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
        return;
      }
      // The registry is a second write target, so a file list on its own no
      // longer describes the switch. Say what moved, capped so it stays one line.
      const envDetail = [
        result.env_written.length > 0 ? `set ${shortList(result.env_written)}` : "",
        result.env_removed.length > 0 ? `cleared ${shortList(result.env_removed)}` : "",
      ]
        .filter(Boolean)
        .join("; ");
      const detail = [
        result.files.join("  •  "),
        envDetail ? `environment ${envDetail}` : "",
        result.removed.length > 0 ? `dropped ${shortList(result.removed)}` : "",
      ]
        .filter(Boolean)
        .join(" — ");
      push("success", `${provider.name} is now in use`, detail);
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

  const handleAdd = useCallback(() => {
    setAddSeq((seq) => seq + 1);
    setView({ mode: "form", provider: null });
  }, []);

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

  const handleToggleBackups = useCallback(
    async (enabled: boolean) => {
      setSavingSettings(true);
      settingsRead.current += 1;
      try {
        setSettings(await backend.setBackupEnabled(enabled));
        push(
          "success",
          enabled ? "Backups are on" : "Backups are off",
          enabled
            ? "A .backup copy is kept beside every file U-Pool writes."
            : "Nothing is copied before a switch overwrites a file.",
        );
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

  const openEnvSettings = useCallback(() => {
    backend.openEnvSettings().catch((error) => push("error", message(error)));
  }, [push]);

  const readSessions = useCallback((target?: string) => {
    const wanted = target ? [target] : Object.keys(SESSION_APPS);
    for (const id of wanted) {
      backend
        .sessionSummary(id as AppId)
        .then((next) => setSessions((current) => ({ ...current, [id]: next })))
        .catch(() => undefined);
    }
  }, []);

  const openSettings = useCallback(() => {
    setSettingsOpen(true);
    // Windows can have switched the startup entry off since the last look.
    const token = (settingsRead.current += 1);
    backend
      .getSettings()
      .then((next) => token === settingsRead.current && setSettings(next))
      .catch(() => undefined);
    backend.checkUpdates(false).then(setUpdate).catch(() => undefined);
    // Transcripts grow while the panel is closed, so the counts are measured on
    // every open rather than cached from the last one.
    readSessions();
    backend.cliVersions().then(setClis).catch(() => undefined);
  }, [readSessions]);

  const handleRefreshClis = useCallback(() => {
    backend
      .refreshCliVersions()
      .then(setClis)
      .catch((error) => push("error", message(error)));
  }, [push]);

  // The probe runs on a background thread, so the answer arrives by polling -
  // the same arrangement the update check uses. Keyed on `ready` as well as
  // `busy`: a probe that finished between two polls flips busy back to false
  // without the result ever having been read.
  useEffect(() => {
    if (!clis || (!clis.busy && clis.ready)) return;
    const timer = setInterval(() => {
      backend.cliVersions().then(setClis).catch(() => undefined);
    }, 500);
    return () => clearInterval(timer);
  }, [clis?.busy, clis?.ready]);

  const handleDeleteSessions = useCallback(
    async (target: string) => {
      setPendingPurge(null);
      setDeletingSessions(target);
      setSessionErrors((current) => ({ ...current, [target]: [] }));
      try {
        const result = await backend.deleteSessions(target as AppId);
        setSessions((current) => ({ ...current, [target]: result.summary }));
        setSessionErrors((current) => ({ ...current, [target]: result.errors }));
        const label = SESSION_APPS[target] ?? target;
        if (result.errors.length > 0) {
          push(
            "error",
            `${label}: ${result.errors.length} left behind`,
            "Close the CLI and try again — the rest were deleted.",
          );
        } else {
          push(
            "success",
            `${label} sessions deleted`,
            `${result.deleted.toLocaleString()} files removed.`,
          );
        }
      } catch (error) {
        push("error", message(error));
      } finally {
        setDeletingSessions("");
      }
    },
    [push],
  );

  // The Cursor tab holds its own state and may never have been opened, so the
  // panel asks for the one field it shows rather than keeping a copy in sync.
  useEffect(() => {
    if (!settingsOpen) return;
    let cancelled = false;
    backend
      .cursorState()
      // Left empty when Cursor is not installed: there would be nothing to
      // reveal, and `open_path` creates the folder it is pointed at.
      .then((next) => !cancelled && setCursorDb(next.supported ? next.db_path : ""))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [settingsOpen]);

  // The registry is only read while the panel that shows it is open, and again
  // if the active app changes underneath it.
  useEffect(() => {
    if (!settingsOpen) return;
    let cancelled = false;
    backend
      .environment(app)
      .then((next) => !cancelled && setEnv(next))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [app, settingsOpen]);

  // Only poll while the backend is actually working; the rest of the time the
  // snapshot cannot change without us asking for it.
  useEffect(() => {
    if (!update?.busy) return;
    const timer = setInterval(() => {
      backend.updateStatus().then(setUpdate).catch(() => undefined);
    }, 600);
    return () => clearInterval(timer);
  }, [update?.busy]);

  // The install has staged the new version and a detached script is waiting for
  // this process to exit. Closing the window is the webview thread's job.
  useEffect(() => {
    if (update?.phase !== "relaunching") return;
    const timer = setTimeout(() => {
      backend.quit().catch(() => undefined);
    }, 600);
    return () => clearTimeout(timer);
  }, [update?.phase]);

  useEffect(() => {
    if (!update) return;
    if (update.installed_from) {
      push("success", `Updated to ${update.current_version}`, `Was ${update.installed_from}.`);
    } else if (update.install_failed) {
      push(
        "error",
        "The last update did not go through",
        update.install_failed === "rolled_back"
          ? "The previous version was put back."
          : `The swap reported "${update.install_failed}".`,
      );
    }
    // Reported once: the backend clears the marker as soon as it has read it.
  }, [update?.installed_from, update?.install_failed]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleInstallUpdate = useCallback(() => {
    backend
      .installUpdate()
      .then(setUpdate)
      .catch((error) => push("error", message(error)));
  }, [push]);

  const handleSkipUpdate = useCallback(
    (version: string) => {
      backend
        .skipUpdate(version)
        .then((next) => {
          setUpdate(next);
          push("success", `Skipped ${version}`, "The next release will be offered again.");
        })
        .catch((error) => push("error", message(error)));
    },
    [push],
  );

  const handleToggleUpdateChecks = useCallback(
    async (enabled: boolean) => {
      setSavingSettings(true);
      try {
        setUpdate(await backend.setUpdateChecks(enabled));
        setSettings(await backend.getSettings());
      } catch (error) {
        push("error", message(error));
      } finally {
        setSavingSettings(false);
      }
    },
    [push],
  );

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
        activeTab={tab}
        version={meta.version}
        clis={clis}
        onSelectTab={selectTab}
        onAdd={handleAdd}
        onTestAll={handleTestAll}
        onOpenFolder={() => state.files[0] && openPath(state.files[0])}
        onOpenSettings={openSettings}
        onRefreshClis={handleRefreshClis}
        testing={testing}
        canOpenFolder={state.files.length > 0}
        updateAvailable={
          update?.phase === "available" &&
          Boolean(update.release) &&
          update.skipped_version !== update.release?.version
        }
      />

      {mockBridge ? (
        <p className="mx-auto mt-2 max-w-[760px] px-4">
          <span className="liquid-pill block rounded-full px-3 py-1.5 text-center text-[12px] font-medium text-amber-900/80">
            Browser preview — in-memory mock, no config files are touched.
          </span>
        </p>
      ) : null}

      {/*
        The Cursor and Usage tabs replace the whole provider view rather than
        sitting inside it: each brings its own page container, its own heading
        and its own state, because neither Cursor accounts nor usage numbers
        live in `AppState`.
      */}
      {tab === "cursor" ? (
        <CursorPool onToast={push} />
      ) : tab === "usage" ? (
        <UsagePanel onToast={push} />
      ) : view.mode === "list" ? (
        <div className="animate-fade-in mx-auto w-full max-w-[720px] px-5 py-6">
          <div className="mb-5 px-1">
            <h1 className="display-title text-[var(--color-label)]">Providers</h1>
            <p className="mt-2 text-[13px] leading-snug tracking-[-0.01em] text-[var(--color-secondary-label)]">
              Select a row to make it active for {appLabel}.
            </p>
          </div>
          {app === "claude_desktop" ? (
            <p className="mb-3 px-1">
              <span className="liquid-pill block rounded-[18px] px-3.5 py-2 text-[12px] font-medium leading-relaxed text-amber-900/80">
                Preview — U-Pool does not write Claude Desktop&apos;s configuration yet. Providers
                you add here are saved and will apply once support lands.
              </span>
            </p>
          ) : null}
          <ProviderList
            providers={state.providers}
            health={health}
            busyId={busyId}
            handlers={handlers}
            onReorder={handleReorder}
          />
        </div>
      ) : (
        // Keyed, so the draft cannot survive from one provider to the next.
        <ProviderForm
          key={view.provider?.id ?? `new-${addSeq}`}
          app={app}
          appLabel={appLabel}
          mode={view.provider ? "edit" : "add"}
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

      {pendingPurge ? (
        <Modal
          title={`Delete every ${SESSION_APPS[pendingPurge] ?? pendingPurge} conversation?`}
          onClose={() => setPendingPurge(null)}
          footer={
            <>
              <Button onClick={() => setPendingPurge(null)}>Cancel</Button>
              <Button variant="danger" onClick={() => handleDeleteSessions(pendingPurge)}>
                Delete {(sessions[pendingPurge]?.files ?? 0).toLocaleString()} files
              </Button>
            </>
          }
        >
          <p>
            This erases {(sessions[pendingPurge]?.files ?? 0).toLocaleString()} files for good.
            Nothing is copied first and there is no undo.
          </p>
          <ul className="mt-3 space-y-1">
            {(sessions[pendingPurge]?.entries ?? [])
              .filter((entry) => entry.exists)
              .map((entry) => (
                <li
                  key={entry.path}
                  className="truncate font-mono text-[11px] text-[var(--color-secondary-label)]"
                  title={entry.path}
                >
                  {entry.path}
                </li>
              ))}
          </ul>
          <p className="mt-3 text-[12px] text-[var(--color-tertiary-label)]">
            Your settings, credentials, plugins and skills are in the same folders and are not
            touched.
          </p>
        </Modal>
      ) : null}

      {settingsOpen ? (
        <SettingsPanel
          version={meta.version}
          platform={meta.platform}
          paths={paths}
          cursorDb={cursorDb}
          liveFiles={state.files}
          settings={settings}
          env={env}
          clis={clis}
          sessions={sessions}
          sessionLabels={SESSION_APPS}
          deletingSessions={deletingSessions}
          sessionErrors={sessionErrors}
          savingSettings={savingSettings}
          update={update}
          onOpen={openPath}
          onToggleStartup={handleToggleStartup}
          onOpenStartupSettings={openStartupSettings}
          onToggleBackups={handleToggleBackups}
          onOpenEnvSettings={openEnvSettings}
          onInstallUpdate={handleInstallUpdate}
          onSkipUpdate={handleSkipUpdate}
          onToggleUpdateChecks={handleToggleUpdateChecks}
          onRefreshClis={handleRefreshClis}
          onDeleteSessions={setPendingPurge}
          onOpenExternal={(url) =>
            backend.openExternal(url).catch((error) => push("error", message(error)))
          }
          onClose={() => setSettingsOpen(false)}
        />
      ) : null}

      {update?.phase === "relaunching" ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-[var(--color-bg)]/85 backdrop-blur-sm">
          <div className="text-center">
            <p className="text-[15px] font-semibold text-[var(--color-label)]">
              Restarting into {update.release?.version ?? "the new version"}
            </p>
            <p className="mt-2 text-[13px] text-[var(--color-secondary-label)]">
              U-Pool will close and open again on its own.
            </p>
          </div>
        </div>
      ) : null}

      <ToastStack toasts={toasts} onDismiss={dismiss} />
    </main>
  );
}
