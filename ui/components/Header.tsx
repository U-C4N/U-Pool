"use client";

import type { AppId, AppInfo } from "@/lib/types";
import { OpenAILogo } from "./BrandMarks";
import { ClaudeGlyph, FolderIcon, GearIcon, PlusIcon, PulseIcon } from "./icons";
import { IconButton, cx } from "./ui";

export function Header({
  apps,
  activeApp,
  version,
  onSelectApp,
  onAdd,
  onTestAll,
  onOpenFolder,
  onOpenSettings,
  testing,
}: {
  apps: AppInfo[];
  activeApp: AppId;
  version?: string;
  onSelectApp: (app: AppId) => void;
  onAdd: () => void;
  onTestAll: () => void;
  onOpenFolder: () => void;
  onOpenSettings: () => void;
  testing: boolean;
}) {
  return (
    <header className="sticky top-0 z-30 px-4 pt-3 pb-2">
      <div className="liquid-glass mx-auto grid max-w-[760px] grid-cols-[1fr_auto_1fr] items-center gap-3 rounded-[22px] px-3.5 py-2.5">
        <div className="relative z-[1] flex items-center gap-0.5">
          <span className="brand-wordmark mr-1">U-Pool</span>
          {version ? (
            <span className="mr-1 rounded-full bg-white/45 px-2 py-0.5 text-[10px] font-semibold tracking-[-0.01em] text-[var(--color-secondary-label)] shadow-[inset_0_0_0_0.5px_rgba(255,255,255,0.55)]">
              v{version.replace(/^v/i, "")}
            </span>
          ) : null}
          <IconButton label="Settings" onClick={onOpenSettings}>
            <GearIcon className="h-[18px] w-[18px]" />
          </IconButton>
        </div>

        <nav
          aria-label="Target application"
          className="liquid-pill relative z-[1] flex items-center rounded-full p-1"
        >
          {apps.map((app) => {
            const active = app.id === activeApp;
            return (
              <button
                key={app.id}
                type="button"
                aria-current={active ? "page" : undefined}
                onClick={() => onSelectApp(app.id)}
                className={cx(
                  "pressable relative flex h-8 items-center gap-1.5 rounded-full px-3.5 text-[13px] font-semibold tracking-[-0.02em]",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
                  active
                    ? "bg-white/90 text-[var(--color-label)] shadow-[0_4px_14px_-6px_rgba(15,23,42,0.35),inset_0_1px_0_rgba(255,255,255,0.9)]"
                    : "text-[var(--color-secondary-label)] hover:text-[var(--color-label)]",
                )}
              >
                {app.id === "codex" ? (
                  <OpenAILogo
                    className={cx(
                      "h-3.5 w-3.5",
                      active ? "text-zinc-900" : "text-[var(--color-tertiary-label)]",
                    )}
                  />
                ) : (
                  <ClaudeGlyph
                    className={cx(
                      "h-3.5 w-3.5",
                      active ? "text-[#D97757]" : "text-[var(--color-tertiary-label)]",
                    )}
                  />
                )}
                {app.label}
              </button>
            );
          })}
        </nav>

        <div className="relative z-[1] flex items-center justify-end gap-2">
          <div className="liquid-pill flex items-center rounded-full p-1">
            <IconButton label="Test every provider" onClick={onTestAll} disabled={testing}>
              <PulseIcon className={cx("h-[18px] w-[18px]", testing && "animate-pulse")} />
            </IconButton>
            <IconButton label="Open config folder" onClick={onOpenFolder}>
              <FolderIcon className="h-[18px] w-[18px]" />
            </IconButton>
          </div>
          <button
            type="button"
            onClick={onAdd}
            aria-label="Add provider"
            title="Add provider"
            className="pressable grid h-9 w-9 place-items-center rounded-full bg-brand-600 text-white shadow-[0_8px_22px_-6px_rgba(0,122,255,0.7),inset_0_1px_0_rgba(255,255,255,0.35)] hover:bg-brand-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
          >
            <PlusIcon className="h-5 w-5" strokeWidth={2.25} />
          </button>
        </div>
      </div>
    </header>
  );
}
