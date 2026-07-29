"use client";

import type { AppPaths, AppSettings } from "@/lib/types";
import { FolderIcon } from "./icons";
import { Button, IconButton, Modal, Switch } from "./ui";

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
  onOpen,
  onToggleStartup,
  onOpenStartupSettings,
  onClose,
}: {
  version: string;
  platform: string;
  paths: AppPaths | null;
  liveFiles: string[];
  settings: AppSettings | null;
  savingSettings: boolean;
  onOpen: (path: string) => void;
  onToggleStartup: (enabled: boolean) => void;
  onOpenStartupSettings: () => void;
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
          Every switch backs up the file it is about to overwrite, keeping the ten most recent copies
          per file.
        </p>
      </div>
    </Modal>
  );
}
