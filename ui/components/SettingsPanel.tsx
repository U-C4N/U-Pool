"use client";

import { useEffect, useState } from "react";
import { backend } from "@/lib/bridge";
import type {
  AppPaths,
  AppSettings,
  CliVersions,
  EnvInfo,
  PricingInfo,
  SessionSummary,
  UpdateStatus,
} from "@/lib/types";
import { DownloadIcon, FolderIcon, RefreshIcon, TrashIcon } from "./icons";
import { Button, IconButton, Modal, Switch, cx } from "./ui";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Bytes as the shortest thing that is still honest at a glance. */
function humanBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const power = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** power;
  return `${value >= 10 || power === 0 ? Math.round(value) : value.toFixed(1)} ${units[power]}`;
}

/** `~/.claude/projects` reads better than the absolute path in a narrow row. */
function shortenPath(path: string, home: string): string {
  const normal = path.replace(/\\/g, "/");
  const root = home.replace(/\\/g, "/").replace(/\/+$/, "");
  return root && normal.startsWith(`${root}/`) ? `~${normal.slice(root.length)}` : normal;
}

/**
 * `cursor.json` sits beside `config.json` in U-Pool's own folder, so the panel
 * builds it from the path it already has rather than inventing a directory:
 * `app_paths` predates the Cursor pool and still reports the provider store
 * only. If it ever names this file, take it from there instead.
 */
function siblingFile(path: string, name: string): string {
  const cut = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return cut < 0 ? name : `${path.slice(0, cut + 1)}${name}`;
}

function CliRow({ tool }: { tool: CliVersions["tools"][number] }) {
  return (
    <div className="flex items-center gap-3 rounded-[10px] bg-[var(--color-fill)] px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium text-[var(--color-label)]">{tool.label}</p>
        <p
          className="truncate font-mono text-[11px] text-[var(--color-secondary-label)]"
          title={tool.path || tool.error}
        >
          {tool.path || tool.error || "—"}
        </p>
      </div>
      <span
        className={cx(
          "shrink-0 rounded-full px-2 py-0.5 font-mono text-[11px] font-semibold",
          tool.version
            ? "bg-white/50 text-[var(--color-label)]"
            : "bg-white/40 text-[var(--color-tertiary-label)]",
        )}
      >
        {tool.version || (tool.found ? "unknown" : "not found")}
      </span>
    </div>
  );
}

/**
 * One app's transcripts, with the size measured rather than described.
 *
 * The count is the whole argument for pressing the button, so it is read fresh
 * every time the panel opens and again after a purge - a row that still claims
 * 230 MB after deleting it reads as a failure.
 */
function SessionRow({
  label,
  summary,
  home,
  busy,
  errors,
  onDelete,
}: {
  label: string;
  summary: SessionSummary | null;
  home: string;
  busy: boolean;
  errors: string[];
  onDelete: () => void;
}) {
  const empty = summary !== null && summary.files === 0;
  const places = (summary?.entries ?? [])
    .map((entry) => shortenPath(entry.path, home))
    .join(", ");
  return (
    <div className="rounded-[10px] bg-[var(--color-fill)] px-3 py-2.5">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium text-[var(--color-label)]">
            {label} conversations
          </p>
          <p className="mt-0.5 text-[11px] leading-relaxed text-[var(--color-secondary-label)]">
            {summary === null
              ? "Measuring…"
              : empty
                ? "Nothing to delete."
                : `${summary.files.toLocaleString()} files · ${humanBytes(summary.bytes)}. Deleted for good — no backup is kept.`}
          </p>
          {places ? (
            <p
              className="mt-1 truncate font-mono text-[10.5px] text-[var(--color-tertiary-label)]"
              title={places}
            >
              {places}
            </p>
          ) : null}
        </div>
        <Button
          variant="danger"
          className="h-8 shrink-0 px-3 text-xs"
          disabled={busy || summary === null || empty}
          onClick={onDelete}
        >
          <TrashIcon className="h-4 w-4" />
          {busy ? "Deleting…" : "Delete all sessions"}
        </Button>
      </div>
      {errors.length > 0 ? (
        <p className="mt-2 rounded-[8px] bg-red-500/[0.07] px-2.5 py-2 text-[11px] leading-relaxed text-red-700">
          {errors.length} item{errors.length === 1 ? "" : "s"} could not be deleted — the CLI is
          probably still running. {errors[0]}
        </p>
      ) : null}
    </div>
  );
}

const BUSY_LABEL: Record<string, string> = {
  checking: "Checking for updates…",
  downloading: "Downloading",
  verifying: "Verifying",
  staging: "Unpacking",
  relaunching: "Restarting",
};

function UpdateSection({
  update,
  checksEnabled,
  savingSettings,
  onInstall,
  onSkip,
  onOpenNotes,
  onToggleChecks,
}: {
  update: UpdateStatus | null;
  checksEnabled: boolean;
  savingSettings: boolean;
  onInstall: () => void;
  onSkip: (version: string) => void;
  onOpenNotes: (url: string) => void;
  onToggleChecks: (enabled: boolean) => void;
}) {
  const rel = update?.release ?? null;
  const version = rel?.version ?? "";
  const skipped = Boolean(version) && update?.skipped_version === version;
  const offered = update?.phase === "available" && Boolean(version) && !skipped;
  const busyLabel = update ? BUSY_LABEL[update.phase] : undefined;

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">Updates</p>
      <div className="rounded-[10px] bg-[var(--color-fill)] px-3 py-2.5">
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-medium text-[var(--color-label)]">
              {offered
                ? `U-Pool ${version} is available`
                : busyLabel ?? (update?.phase === "up_to_date" ? "U-Pool is up to date" : "Updates")}
            </p>
            <p className="mt-0.5 text-[11px] leading-relaxed text-[var(--color-secondary-label)]">
              {update?.error ||
                update?.detail ||
                (skipped
                  ? `Version ${version} was skipped. It will be offered again next release.`
                  : offered && !update?.can_install
                    ? update.blocker
                    : "Checked against the GitHub releases page every few hours.")}
            </p>
          </div>
          {offered ? (
            update.can_install ? (
              <Button
                variant="primary"
                className="animate-attention shrink-0"
                onClick={onInstall}
              >
                <DownloadIcon className="h-4 w-4" />
                Update
              </Button>
            ) : (
              <Button className="shrink-0" onClick={() => onOpenNotes(rel!.html_url)}>
                Open release page
              </Button>
            )
          ) : null}
        </div>

        {update && update.percent !== null && update.busy ? (
          <div className="mt-2.5 h-1 w-full overflow-hidden rounded-full bg-black/10">
            <div
              className="h-full rounded-full bg-brand-600 transition-[width] duration-300"
              style={{ width: `${Math.min(100, Math.max(0, update.percent))}%` }}
            />
          </div>
        ) : null}

        {update?.verified && update.phase !== "available" ? (
          <p className="mt-2 text-[11px] text-[var(--color-tertiary-label)]">
            Verified against {update.verified}.
          </p>
        ) : null}

        {offered ? (
          <div className="mt-2.5 flex items-center gap-3">
            <button
              type="button"
              className="text-[11px] font-medium text-[var(--color-secondary-label)] hover:text-[var(--color-label)]"
              onClick={() => onSkip(version)}
            >
              Skip this version
            </button>
            <button
              type="button"
              className="text-[11px] font-medium text-brand-600 hover:underline"
              onClick={() => onOpenNotes(rel!.html_url)}
            >
              Release notes
            </button>
          </div>
        ) : null}
      </div>

      <div className="flex items-center gap-3 rounded-[10px] bg-[var(--color-fill)] px-3 py-2.5">
        <p className="min-w-0 flex-1 text-[13px] font-medium text-[var(--color-label)]">
          Check for updates automatically
        </p>
        <Switch
          label="Check for updates automatically"
          checked={checksEnabled}
          busy={savingSettings}
          onChange={onToggleChecks}
        />
      </div>
    </div>
  );
}

/**
 * One managed registry value. `owned` is the interesting bit: a name U-Pool did
 * not set is shown so the list matches the registry, but it survives a switch.
 */
function EnvRow({ name, value, owned }: { name: string; value: string; owned: boolean }) {
  return (
    <div className="flex items-center gap-3 rounded-[10px] bg-[var(--color-fill)] px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="truncate font-mono text-[12px] font-medium text-[var(--color-label)]">{name}</p>
        <p className="truncate font-mono text-[11px] text-[var(--color-secondary-label)]" title={value}>
          {value || "—"}
        </p>
      </div>
      {owned ? null : (
        <span className="shrink-0 rounded-full bg-white/50 px-2 py-0.5 text-[10px] font-medium text-[var(--color-secondary-label)]">
          set outside U-Pool
        </span>
      )}
    </div>
  );
}

function PathRow({ label, value, onOpen }: { label: string; value: string; onOpen: () => void }) {
  return (
    <div className="flex items-center gap-3 rounded-[10px] bg-[var(--color-fill)] px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-medium text-[var(--color-secondary-label)]">{label}</p>
        <p className="truncate font-mono text-[12px] text-[var(--color-label)]" title={value}>
          {value}
        </p>
      </div>
      <IconButton label={`Open ${label}`} onClick={onOpen}>
        <FolderIcon className="h-4.5 w-4.5" />
      </IconButton>
    </div>
  );
}

const RATE_KINDS = ["input", "output", "cache_read", "cache_write"] as const;
type RateKind = (typeof RATE_KINDS)[number];
const RATE_KIND_LABEL: Record<RateKind, string> = {
  input: "Input",
  output: "Output",
  cache_read: "Cache read",
  cache_write: "Cache write",
};

/** What the user typed, kept as text so a cell can be empty rather than 0. */
type PricingDraft = Record<string, Partial<Record<RateKind, string>>>;

/** Only the kinds a model actually has an override for become draft text - an
 * untouched cell stays empty and falls back to the merged rate's placeholder. */
function draftFromOverrides(overrides: Record<string, Record<string, number>>): PricingDraft {
  const draft: PricingDraft = {};
  for (const [model, rates] of Object.entries(overrides)) {
    const row: Partial<Record<RateKind, string>> = {};
    for (const kind of RATE_KINDS) {
      if (rates[kind] !== undefined) row[kind] = String(rates[kind]);
    }
    draft[model] = row;
  }
  return draft;
}

/** The merged/built-in rate shown as a placeholder - whole numbers stay whole. */
function formatRate(value: number | undefined): string {
  if (value === undefined) return "";
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function PricingRateInput({
  value,
  placeholder,
  onChange,
}: {
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  return (
    <input
      type="number"
      inputMode="decimal"
      step="0.01"
      min="0"
      value={value}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
      className={cx(
        "h-8 w-full rounded-[8px] bg-white/55 px-2 text-right text-[12px] tabular-nums text-[var(--color-label)]",
        "placeholder:text-[var(--color-tertiary-label)] hover:bg-white/70",
        "focus:bg-white/90 focus:outline-none focus:shadow-[0_0_0_2.5px_rgba(0,122,255,0.16),0_0_0_1px_rgba(0,122,255,0.4)]",
      )}
    />
  );
}

/**
 * The per-model price table. Self-contained rather than lifted into the page
 * component - like `UsagePanel`, it reads and writes the bridge on its own and
 * only needs a path to open and a place to put the file.
 */
function PricingSection({ pricingPath, onOpen }: { pricingPath: string; onOpen: (path: string) => void }) {
  const [pricing, setPricing] = useState<PricingInfo | null>(null);
  const [draft, setDraft] = useState<PricingDraft>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    backend
      .getPricing()
      .then((info) => {
        if (cancelled) return;
        setPricing(info);
        setDraft(draftFromOverrides(info.overrides));
      })
      .catch((error) => !cancelled && setLoadError(message(error)));
    return () => {
      cancelled = true;
    };
  }, []);

  const handleCellChange = (model: string, kind: RateKind, value: string) => {
    setDraft((prev) => ({ ...prev, [model]: { ...prev[model], [kind]: value } }));
    setSaveError(null);
  };

  // Rebuilds the whole overrides object from the draft rather than diffing
  // against the last save - `set_pricing` replaces pricing.json wholesale, so
  // an untouched override has to survive by staying in the draft, and a
  // cleared cell has to drop out of it, on every save.
  const handleSave = () => {
    const payload: Record<string, Record<string, number>> = {};
    for (const [model, rates] of Object.entries(draft)) {
      const kinds: Record<string, number> = {};
      for (const kind of RATE_KINDS) {
        const raw = (rates[kind] ?? "").trim();
        if (raw === "") continue;
        const num = Number(raw);
        if (!Number.isFinite(num) || num < 0) {
          setSaveError(`${model} ${RATE_KIND_LABEL[kind]}: enter a rate of 0 or more, or leave it empty.`);
          return;
        }
        kinds[kind] = num;
      }
      if (Object.keys(kinds).length > 0) payload[model] = kinds;
    }
    setSaving(true);
    setSaveError(null);
    backend
      .setPricing(payload)
      .then((info) => {
        setPricing(info);
        setDraft(draftFromOverrides(info.overrides));
      })
      .catch((error) => setSaveError(message(error)))
      .finally(() => setSaving(false));
  };

  const models = pricing ? Object.keys(pricing.merged) : [];

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">Pricing</p>
        <Button
          className="h-7 px-2.5 text-[11px]"
          disabled={!pricingPath}
          onClick={() => onOpen(pricingPath)}
        >
          <FolderIcon className="h-3.5 w-3.5" />
          Open file
        </Button>
      </div>
      <p className="text-[11px] leading-relaxed text-zinc-400">
        $ per 1,000,000 tokens, used to price the Usage tab. Faint numbers are U-Pool's built-in
        rates; type over one to override it, or clear a cell to fall back to the built-in rate.
      </p>

      {loadError ? (
        <p className="rounded-[8px] bg-red-500/[0.07] px-2.5 py-2 text-[11px] leading-relaxed text-red-700">
          {loadError}
        </p>
      ) : !pricing ? (
        <p className="text-xs text-zinc-400">Loading...</p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-[10px] bg-[var(--color-fill)] px-3 py-2.5">
            <table className="w-full min-w-[440px] border-collapse text-[12px]">
              <thead>
                <tr>
                  <th
                    scope="col"
                    className="pb-1.5 pr-2 text-left text-[10.5px] font-semibold uppercase tracking-[0.04em] text-[var(--color-tertiary-label)]"
                  >
                    Model
                  </th>
                  {RATE_KINDS.map((kind) => (
                    <th
                      key={kind}
                      scope="col"
                      className="pb-1.5 pl-2 text-right text-[10.5px] font-semibold uppercase tracking-[0.04em] text-[var(--color-tertiary-label)]"
                    >
                      {RATE_KIND_LABEL[kind]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {models.map((model) => (
                  <tr key={model} className="border-t border-[var(--color-separator)] first:border-0">
                    <td
                      className="max-w-[140px] truncate py-1.5 pr-2 font-mono text-[11.5px] text-[var(--color-label)]"
                      title={model}
                    >
                      {model}
                    </td>
                    {RATE_KINDS.map((kind) => (
                      <td key={kind} className="py-1.5 pl-2">
                        <PricingRateInput
                          value={draft[model]?.[kind] ?? ""}
                          placeholder={formatRate(pricing.merged[model]?.[kind])}
                          onChange={(value) => handleCellChange(model, kind, value)}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {saveError ? (
            <p className="rounded-[8px] bg-red-500/[0.07] px-2.5 py-2 text-[11px] leading-relaxed text-red-700">
              {saveError}
            </p>
          ) : null}

          <div className="flex justify-end">
            <Button variant="primary" className="h-8 px-3 text-xs" disabled={saving} onClick={handleSave}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

export function SettingsPanel({
  version,
  platform,
  paths,
  cursorDb,
  liveFiles,
  settings,
  savingSettings,
  update,
  env,
  clis,
  sessions,
  sessionLabels,
  deletingSessions,
  sessionErrors,
  onOpen,
  onToggleStartup,
  onOpenStartupSettings,
  onToggleBackups,
  onOpenEnvSettings,
  onInstallUpdate,
  onSkipUpdate,
  onToggleUpdateChecks,
  onRefreshClis,
  onDeleteSessions,
  onOpenExternal,
  onClose,
}: {
  version: string;
  platform: string;
  paths: AppPaths | null;
  /** Cursor's state database, or empty when Cursor is not installed here. */
  cursorDb: string;
  liveFiles: string[];
  settings: AppSettings | null;
  savingSettings: boolean;
  update: UpdateStatus | null;
  /** The registry side of the active app's config; null until it has been read. */
  env: EnvInfo | null;
  clis: CliVersions | null;
  /** Per-app transcript counts, keyed by app id; absent until measured. */
  sessions: Record<string, SessionSummary>;
  sessionLabels: Record<string, string>;
  deletingSessions: string;
  sessionErrors: Record<string, string[]>;
  onOpen: (path: string) => void;
  onToggleStartup: (enabled: boolean) => void;
  onOpenStartupSettings: () => void;
  onToggleBackups: (enabled: boolean) => void;
  onOpenEnvSettings: () => void;
  onInstallUpdate: () => void;
  onSkipUpdate: (version: string) => void;
  onToggleUpdateChecks: (enabled: boolean) => void;
  onRefreshClis: () => void;
  onDeleteSessions: (app: string) => void;
  onOpenExternal: (url: string) => void;
  onClose: () => void;
}) {
  const startupSupported = Boolean(settings?.autostart_supported);
  const home = paths?.home ?? "";
  return (
    <Modal title="Settings" onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      <div className="space-y-4">
        <div className="flex items-center justify-between text-[12px] text-[var(--color-secondary-label)]">
          <span>
            U-Pool{" "}
            <span className="rounded-full bg-white/50 px-2 py-0.5 font-mono text-[11px] font-semibold text-[var(--color-label)]">
              v{version.replace(/^v/i, "") || "—"}
            </span>
          </span>
          <span className="font-mono text-[11px]">{platform}</span>
        </div>

        <UpdateSection
          update={update}
          checksEnabled={settings?.update_check_enabled ?? true}
          savingSettings={savingSettings}
          onInstall={onInstallUpdate}
          onSkip={onSkipUpdate}
          onOpenNotes={onOpenExternal}
          onToggleChecks={onToggleUpdateChecks}
        />

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
              Installed CLIs
            </p>
            <IconButton
              label={clis?.busy ? "Checking…" : "Check again"}
              className="h-7 w-7"
              disabled={Boolean(clis?.busy)}
              onClick={onRefreshClis}
            >
              <RefreshIcon className={cx("h-4 w-4", clis?.busy && "animate-spin")} />
            </IconButton>
          </div>
          {(clis?.tools ?? []).map((tool) => (
            <CliRow key={tool.id} tool={tool} />
          ))}
          {clis && !clis.ready ? (
            <p className="text-xs text-zinc-400">Looking for them on this machine…</p>
          ) : null}
        </div>

        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">Sessions</p>
          {Object.entries(sessionLabels).map(([app, label]) => (
            <SessionRow
              key={app}
              label={label}
              summary={sessions[app] ?? null}
              home={home}
              busy={deletingSessions === app}
              errors={sessionErrors[app] ?? []}
              onDelete={() => onDeleteSessions(app)}
            />
          ))}
          <p className="text-[11px] leading-relaxed text-zinc-400">
            Transcripts and prompt history only. Your settings, credentials, plugins and skills
            live in the same folders and are left exactly as they are.
          </p>
        </div>

        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">Startup</p>
          <div className="rounded-[10px] bg-[var(--color-fill)] px-3 py-2.5">
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium text-[var(--color-label)]">
                  Open U-Pool when I sign in
                </p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-[var(--color-secondary-label)]">
                  {settings?.autostart_detail ?? "Checking..."}
                </p>
              </div>
              <Switch
                label="Open U-Pool when I sign in"
                checked={Boolean(settings?.launch_at_startup)}
                disabled={!startupSupported}
                busy={savingSettings}
                onChange={onToggleStartup}
              />
            </div>
            {settings?.autostart_blocked ? (
              <div className="mt-2.5 flex items-center gap-2">
                <Button className="h-8 px-3 text-xs" onClick={onOpenStartupSettings}>
                  Open Windows startup settings
                </Button>
                <span className="text-[11px] text-[var(--color-tertiary-label)]">
                  or switch it off here to remove the entry
                </span>
              </div>
            ) : settings?.launch_at_startup && settings.autostart_command ? (
              <p
                className="mt-2 truncate font-mono text-[11px] text-[var(--color-tertiary-label)]"
                title={settings.autostart_command}
              >
                {settings.autostart_command}
              </p>
            ) : null}
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">Backups</p>
          <div className="flex items-center gap-3 rounded-[10px] bg-[var(--color-fill)] px-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="text-[13px] font-medium text-[var(--color-label)]">
                Keep a copy of every file U-Pool changes
              </p>
              <p className="mt-0.5 text-[11px] leading-relaxed text-[var(--color-secondary-label)]">
                One copy beside the original, overwritten on every change —
                ~/.claude/settings.json.backup, ~/.codex/config.toml.backup,
                ~/.codex/auth.json.backup. Nothing is written while this is off.
              </p>
            </div>
            <Switch
              label="Keep a copy of every file U-Pool changes"
              checked={Boolean(settings?.backup_enabled)}
              busy={savingSettings}
              onChange={onToggleBackups}
            />
          </div>
          {paths ? (
            <PathRow
              label="Registry snapshot folder"
              value={paths.backups}
              onOpen={() => onOpen(paths.backups)}
            />
          ) : null}
        </div>

        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">U-Pool data</p>
          {paths ? (
            <>
              <PathRow label="Provider store" value={paths.config} onOpen={() => onOpen(paths.config)} />
              <PathRow label="Preferences" value={paths.settings} onOpen={() => onOpen(paths.settings)} />
            </>
          ) : (
            <p className="text-xs text-zinc-400">Loading...</p>
          )}
        </div>

        {paths ? <PricingSection pricingPath={paths.pricing} onOpen={onOpen} /> : null}

        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">Cursor</p>
          {paths ? (
            <PathRow
              label="Account pool"
              value={siblingFile(paths.config, "cursor.json")}
              onOpen={() => onOpen(siblingFile(paths.config, "cursor.json"))}
            />
          ) : null}
          {cursorDb ? (
            <PathRow label="State database" value={cursorDb} onOpen={() => onOpen(cursorDb)} />
          ) : (
            <p className="text-xs text-zinc-400">
              U-Pool did not find Cursor here, so there is no state database to open. The Cursor
              tab names the path it looked in.
            </p>
          )}
          <p className="text-[11px] leading-relaxed text-zinc-400">
            A switch rewrites the cursorAuth keys inside state.vscdb and nothing else — your
            conversations, composer state and MCP secrets are in the same file and are left exactly
            as they are.
          </p>
        </div>

        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
            Live config for this app
          </p>
          {liveFiles.length > 0 ? (
            liveFiles.map((file) => (
              <PathRow key={file} label="Written on switch" value={file} onOpen={() => onOpen(file)} />
            ))
          ) : (
            <p className="text-xs text-zinc-400">U-Pool does not write a file for this app yet.</p>
          )}
        </div>

        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
            Windows environment
          </p>
          {!env ? (
            <p className="text-xs text-zinc-400">Loading...</p>
          ) : !env.namespace ? (
            // An app with no namespace reports supported=false whatever the OS is,
            // so this has to come first - otherwise Claude Desktop tells a Windows
            // user that environment variables are a Windows-only feature.
            <p className="text-xs text-zinc-400">
              U-Pool does not set environment variables for this app yet.
            </p>
          ) : !env.supported ? (
            <p className="text-xs text-zinc-400">
              Environment variables are wired up for Windows only.
            </p>
          ) : (
            <>
              {env.vars.length > 0 ? (
                env.vars.map((entry) => (
                  <EnvRow
                    key={entry.name}
                    name={entry.name}
                    value={entry.value_masked}
                    owned={entry.owned}
                  />
                ))
              ) : (
                <p className="text-xs text-zinc-400">
                  Nothing set yet — the next switch writes what this app needs.
                </p>
              )}
              <Button className="h-8 px-3 text-xs" onClick={onOpenEnvSettings}>
                Open Windows environment variables
              </Button>
            </>
          )}
        </div>

        <p className="text-xs leading-relaxed text-zinc-400">
          A switch changes only the keys U-Pool owns — plugins, theme, MCP servers, project trust
          and everything else in the file stay as they are. It also sets the Windows environment
          variables the CLIs read, because a file alone does not reach them.
        </p>
      </div>
    </Modal>
  );
}
