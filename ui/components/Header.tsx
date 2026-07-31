"use client";

import type { AppId, AppInfo } from "@/lib/types";
import { OpenAILogo } from "./BrandMarks";
import {
  ClaudeDesktopGlyph,
  ClaudeGlyph,
  FolderIcon,
  GearIcon,
  PlusIcon,
  PulseIcon,
} from "./icons";
import { IconButton, cx } from "./ui";

/**
 * A mark needs nothing but a class, which is what lets one lookup hold both the
 * inline SVGs and the mask-based brand marks.
 */
const TAB_MARK: Record<AppId, React.ComponentType<{ className?: string }>> = {
  claude: ClaudeGlyph,
  claude_desktop: ClaudeDesktopGlyph,
  codex: OpenAILogo,
};

/** The OpenAI mark is black in its own right; the Anthropic ones take the clay. */
const TAB_TINT: Record<AppId, string> = {
  claude: "text-[#D97757]",
  claude_desktop: "text-[#D97757]",
  codex: "text-zinc-900",
};

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
  canOpenFolder = true,
  updateAvailable = false,
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
  /** False for an app with no live config, so the button is not silently dead. */
  canOpenFolder?: boolean;
  /** Puts a dot on the gear, since the offer itself lives inside Settings. */
  updateAvailable?: boolean;
}) {
  return (
    <header className="upool-header z-30">
      <div className="mx-auto flex h-14 max-w-[760px] items-center gap-3 px-5">
        <div className="flex min-w-0 flex-1 items-baseline gap-2">
          <span className="brand-wordmark shrink-0">U-Pool</span>
          {version ? (
            <span className="truncate text-[11px] font-medium tracking-[-0.01em] text-[var(--color-tertiary-label)]">
              {version.replace(/^v/i, "")}
            </span>
          ) : null}
        </div>

        <nav
          aria-label="Target application"
          className="upool-segment relative flex shrink-0 items-center p-0.5"
        >
          {apps.map((app) => {
            const active = app.id === activeApp;
            const Mark = TAB_MARK[app.id];
            return (
              <button
                key={app.id}
                type="button"
                aria-current={active ? "page" : undefined}
                onClick={() => onSelectApp(app.id)}
                className={cx(
                  "pressable relative z-[1] flex h-8 items-center gap-1.5 rounded-[9px] px-3 text-[13px] font-semibold tracking-[-0.02em]",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
                  active
                    ? "text-[var(--color-label)]"
                    : "text-[var(--color-secondary-label)] hover:text-[var(--color-label)]",
                )}
              >
                {active ? <span aria-hidden className="upool-segment-thumb" /> : null}
                <span className="relative z-[1] flex items-center gap-1.5">
                  <Mark
                    className={cx(
                      "h-3.5 w-3.5",
                      active ? TAB_TINT[app.id] : "text-[var(--color-tertiary-label)]",
                    )}
                  />
                  {app.label}
                </span>
              </button>
            );
          })}
        </nav>

        <div className="flex flex-1 items-center justify-end gap-0.5">
          <IconButton label="Test every provider" onClick={onTestAll} disabled={testing}>
            <PulseIcon className={cx("h-[18px] w-[18px]", testing && "animate-pulse")} />
          </IconButton>
          <IconButton
            label={canOpenFolder ? "Open config folder" : "This app has no config file yet"}
            onClick={onOpenFolder}
            disabled={!canOpenFolder}
          >
            <FolderIcon className="h-[18px] w-[18px]" />
          </IconButton>
          <span className="relative inline-flex">
            <IconButton
              label={updateAvailable ? "Settings — an update is available" : "Settings"}
              onClick={onOpenSettings}
            >
              <GearIcon className="h-[18px] w-[18px]" />
            </IconButton>
            {updateAvailable ? (
              <span
                aria-hidden
                className="animate-attention pointer-events-none absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-[#FF9500]"
              />
            ) : null}
          </span>
          <button
            type="button"
            onClick={onAdd}
            aria-label="Add provider"
            title="Add provider"
            className="pressable ml-1.5 grid h-8 w-8 place-items-center rounded-[10px] bg-brand-600 text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.28)] hover:bg-brand-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
          >
            <PlusIcon className="h-[18px] w-[18px]" strokeWidth={2.25} />
          </button>
        </div>
      </div>
    </header>
  );
}
