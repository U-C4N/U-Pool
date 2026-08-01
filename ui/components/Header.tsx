"use client";

import type { AppId, AppInfo, CliVersions } from "@/lib/types";
import { HermesLogo, OpenAILogo, OpenCodeLogo } from "./BrandMarks";
import {
  ClaudeDesktopGlyph,
  ClaudeGlyph,
  FolderIcon,
  GearIcon,
  PlusIcon,
  PulseIcon,
  RefreshIcon,
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
  hermes: HermesLogo,
  opencode: OpenCodeLogo,
};

/** The OpenAI mark is black in its own right; the Anthropic ones take the clay. */
const TAB_TINT: Record<AppId, string> = {
  claude: "text-[#D97757]",
  claude_desktop: "text-[#D97757]",
  codex: "text-zinc-900",
  hermes: "text-[#7C3AED]",
  opencode: "text-zinc-900",
};

/**
 * One installed CLI, as a short mono pill.
 *
 * A tool that is not installed still gets a slot. Hiding it would make the row
 * silently shorter and leave the user unable to tell "no Codex here" from "the
 * probe has not run yet", which is the question the refresh button exists for.
 */
function CliPill({ id, label, version, path, found, error, ready }: {
  id: string;
  label: string;
  version: string;
  path: string;
  found: boolean;
  error: string;
  ready: boolean;
}) {
  // `?` rather than `—` when the binary is on disk but said nothing usable: a
  // broken shim is a different thing to go and fix than a missing install.
  const state = !ready ? "…" : version || (found ? "?" : "—");
  const title = version
    ? `${label} ${version}${path ? `\n${path}` : ""}`
    : error || (ready ? `${label} is not installed` : `Checking ${label}…`);
  return (
    <span
      title={title}
      className={cx(
        "shrink-0 font-mono text-[10.5px] leading-none tracking-[-0.01em]",
        version ? "text-[var(--color-secondary-label)]" : "text-[var(--color-tertiary-label)]",
      )}
    >
      {id} {state}
    </span>
  );
}

export function Header({
  apps,
  activeApp,
  version,
  clis,
  onSelectApp,
  onAdd,
  onTestAll,
  onOpenFolder,
  onOpenSettings,
  onRefreshClis,
  testing,
  canOpenFolder = true,
  updateAvailable = false,
}: {
  apps: AppInfo[];
  activeApp: AppId;
  version?: string;
  /** Which version of each target CLI is installed; null until bootstrap lands. */
  clis: CliVersions | null;
  onSelectApp: (app: AppId) => void;
  onAdd: () => void;
  onTestAll: () => void;
  onOpenFolder: () => void;
  onOpenSettings: () => void;
  onRefreshClis: () => void;
  testing: boolean;
  /** False for an app with no live config, so the button is not silently dead. */
  canOpenFolder?: boolean;
  /** Puts a dot on the gear, since the offer itself lives inside Settings. */
  updateAvailable?: boolean;
}) {
  const probing = Boolean(clis?.busy);
  return (
    /**
     * Two rows, not one. Five app tabs come to roughly 660px on their own, and a
     * single row also had to hold the wordmark, two version readouts and five
     * buttons - which overflowed a 1180px window: the second version pill was
     * clipped mid-word and the button cluster was pushed off the right edge
     * entirely. Splitting them gives each row room to spare and lets the window
     * still narrow to 940.
     */
    <header className="upool-header z-30">
      <div className="mx-auto flex h-11 w-full max-w-[720px] items-center gap-3 px-5 pt-1">
        <div className="flex min-w-0 flex-1 items-baseline gap-2.5 overflow-hidden">
          <span className="brand-wordmark shrink-0">U-Pool</span>
          {version ? (
            <span className="shrink-0 text-[11px] font-medium tracking-[-0.01em] text-[var(--color-tertiary-label)]">
              {version.replace(/^v/i, "")}
            </span>
          ) : null}
          {(clis?.tools ?? []).length > 0 ? (
            <span aria-hidden className="h-3 w-px shrink-0 bg-black/[0.08]" />
          ) : null}
          {(clis?.tools ?? []).map((tool) => (
            <CliPill key={tool.id} ready={Boolean(clis?.ready)} {...tool} />
          ))}
        </div>

        <div className="flex shrink-0 items-center gap-0.5">
          <IconButton
            label={probing ? "Checking installed versions…" : "Check installed CLI versions"}
            onClick={onRefreshClis}
            disabled={probing}
          >
            <RefreshIcon className={cx("h-[18px] w-[18px]", probing && "animate-spin")} />
          </IconButton>
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

      <div className="flex justify-center px-5 pb-2">
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
      </div>
    </header>
  );
}
