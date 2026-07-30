"use client";

import type { AppPaths, AppSettings, UpdateStatus } from "@/lib/types";
import { DownloadIcon, FolderIcon } from "./icons";
import { Button, IconButton, Modal, Switch } from "./ui";

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

export function SettingsPanel({
  version,
  platform,
  paths,
  liveFiles,
  settings,
  savingSettings,
  update,
  onOpen,
  onToggleStartup,
  onOpenStartupSettings,
  onInstallUpdate,
  onSkipUpdate,
  onToggleUpdateChecks,
  onOpenExternal,
  onClose,
}: {
  version: string;
  platform: string;
  paths: AppPaths | null;
  liveFiles: string[];
  settings: AppSettings | null;
  savingSettings: boolean;
  update: UpdateStatus | null;
  onOpen: (path: string) => void;
  onToggleStartup: (enabled: boolean) => void;
  onOpenStartupSettings: () => void;
  onInstallUpdate: () => void;
  onSkipUpdate: (version: string) => void;
  onToggleUpdateChecks: (enabled: boolean) => void;
  onOpenExternal: (url: string) => void;
  onClose: () => void;
}) {
  const startupSupported = Boolean(settings?.autostart_supported);
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
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">U-Pool data</p>
          {paths ? (
            <>
              <PathRow label="Provider store" value={paths.config} onOpen={() => onOpen(paths.config)} />
              <PathRow label="Preferences" value={paths.settings} onOpen={() => onOpen(paths.settings)} />
              <PathRow label="Backups" value={paths.backups} onOpen={() => onOpen(paths.backups)} />
            </>
          ) : (
            <p className="text-xs text-zinc-400">Loading...</p>
          )}
        </div>

        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
            Live config for this app
          </p>
          {liveFiles.map((file) => (
            <PathRow key={file} label="Written on switch" value={file} onOpen={() => onOpen(file)} />
          ))}
        </div>

        <p className="text-xs leading-relaxed text-zinc-400">
          A switch rewrites the file above from scratch, so it holds only the provider you picked —
          anything else that was in it is removed. The previous version is copied into Backups
          first, which keeps the ten most recent per file plus one permanent pre-0.5.0 copy.
        </p>
      </div>
    </Modal>
  );
}
