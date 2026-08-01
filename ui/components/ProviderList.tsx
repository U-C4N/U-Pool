"use client";

import { useState } from "react";
import type { AppId, HealthResult, HealthStatus, ProviderSummary } from "@/lib/types";
import { AnthropicLogo, OpenAILogo } from "./BrandMarks";
import {
  CheckIcon,
  CopyIcon,
  GripIcon,
  PencilIcon,
  PulseIcon,
  TrashIcon,
} from "./icons";
import { IconButton, cx } from "./ui";

const HEALTH_TONE: Record<HealthStatus, string> = {
  ok: "bg-emerald-500",
  auth: "bg-amber-500",
  warn: "bg-amber-500",
  error: "bg-red-500",
  unreachable: "bg-red-500",
  skipped: "bg-zinc-300",
};

function HealthBadge({ result }: { result: HealthResult }) {
  const label = result.latency_ms !== null ? `${result.latency_ms}ms` : result.message;
  return (
    <span
      title={result.message}
      className="inline-flex items-center gap-1.5 rounded-full bg-white/55 px-2 py-0.5 text-[11px] font-medium tracking-[0.01em] text-[var(--color-secondary-label)] shadow-[inset_0_0_0_0.5px_rgba(255,255,255,0.6)]"
    >
      <span className={cx("h-1.5 w-1.5 rounded-full", HEALTH_TONE[result.status])} />
      {label}
    </span>
  );
}

const BYPASS_HINT: Record<ProviderSummary["app"], string> = {
  claude: "permission prompts are bypassed for this provider",
  claude_desktop: "permission prompts are bypassed for this provider",
  codex: "approvals and the sandbox are bypassed for this provider",
  // Neither has a permission model U-Pool drives, so the badge never renders for
  // them - the entry is here because the record is exhaustive, not because it shows.
  hermes: "",
  opencode: "",
};

/**
 * Claude Desktop carries a Claude Code record, so every rule in this list reads
 * the same for both. Spelled out rather than `!== "codex"` so a fourth app has
 * to declare which side it is on.
 */
function isClaudeApp(app: AppId): boolean {
  return app === "claude" || app === "claude_desktop";
}

/** A provider that has turned every confirmation off for its target CLI. */
function isWideOpen(provider: ProviderSummary): boolean {
  if (provider.official) return false;
  return isClaudeApp(provider.app) ? provider.bypass_permissions : provider.bypass_approvals;
}

function looksOpenAI(provider: ProviderSummary): boolean {
  const hay = `${provider.name} ${provider.website} ${provider.base_url}`.toLowerCase();
  return (
    (provider.official && provider.app === "codex") ||
    hay.includes("openai") ||
    hay.includes("chatgpt")
  );
}

function looksClaude(provider: ProviderSummary): boolean {
  const hay = `${provider.name} ${provider.website} ${provider.base_url}`.toLowerCase();
  return (
    (provider.official && isClaudeApp(provider.app)) ||
    hay.includes("anthropic") ||
    hay.includes("claude")
  );
}

function Avatar({ provider }: { provider: ProviderSummary }) {
  const openAI = looksOpenAI(provider);
  const claude = !openAI && looksClaude(provider);
  const initial = provider.name.trim().charAt(0).toUpperCase() || "?";

  return (
    <span
      aria-hidden="true"
      className={cx(
        "grid h-11 w-11 shrink-0 place-items-center rounded-[14px] text-[14px] font-semibold tracking-tight",
        "bg-white/55 shadow-[inset_0_1px_0_rgba(255,255,255,0.8),0_6px_16px_-10px_rgba(15,23,42,0.45)]",
        provider.active && "ring-2 ring-brand-500/35",
      )}
    >
      {openAI ? (
        <OpenAILogo className="h-[18px] w-[18px] text-zinc-900" />
      ) : claude ? (
        <AnthropicLogo className="h-[18px] w-[18px] text-[#D97757]" />
      ) : (
        <span className="text-[var(--color-secondary-label)]">{initial}</span>
      )}
    </span>
  );
}

export interface RowHandlers {
  onSwitch: (provider: ProviderSummary) => void;
  onEdit: (provider: ProviderSummary) => void;
  onDuplicate: (provider: ProviderSummary) => void;
  onTest: (provider: ProviderSummary) => void;
  onDelete: (provider: ProviderSummary) => void;
  onOpenWebsite: (url: string) => void;
}

function ProviderRow({
  provider,
  health,
  busy,
  dragging,
  handlers,
  dragProps,
}: {
  provider: ProviderSummary;
  health?: HealthResult;
  busy: boolean;
  dragging: boolean;
  handlers: RowHandlers;
  dragProps: React.HTMLAttributes<HTMLLIElement> & { draggable?: boolean };
}) {
  const link = provider.base_url || provider.website;
  return (
    <li
      {...dragProps}
      className={cx(
        "group relative flex items-center gap-3 rounded-[20px] px-3.5 py-3.5 transition-[transform,opacity,background] duration-200",
        "liquid-glass",
        provider.active && "shadow-[0_0_0_1px_rgba(0,122,255,0.22),0_18px_40px_-24px_rgba(0,122,255,0.55)]",
        dragging && "opacity-40 scale-[0.98]",
        busy && "pointer-events-none opacity-60",
      )}
    >
      <button
        type="button"
        disabled={provider.active}
        onClick={() => handlers.onSwitch(provider)}
        className="absolute inset-0 rounded-[20px] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-500"
        title={provider.active ? "Already in use" : `Switch to ${provider.name}`}
      >
        <span className="sr-only">
          {provider.active ? `${provider.name} is in use` : `Switch to ${provider.name}`}
        </span>
      </button>

      <span
        className="relative z-[1] cursor-grab text-[var(--color-tertiary-label)] transition-colors group-hover:text-[var(--color-secondary-label)] active:cursor-grabbing"
        title="Drag to reorder"
      >
        <GripIcon className="h-4 w-4" />
      </span>

      <span className="relative z-[1]">
        <Avatar provider={provider} />
      </span>

      <div className="pointer-events-none relative z-[1] min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[15px] font-semibold tracking-[-0.025em] text-[var(--color-label)]">
            {provider.name}
          </span>
          {provider.official ? (
            <span className="shrink-0 rounded-full bg-white/50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.05em] text-[var(--color-secondary-label)]">
              Official
            </span>
          ) : null}
          {isWideOpen(provider) ? (
            <span
              // No pointer-events-auto: the row content stays inert so a click
              // anywhere on the row still switches provider. The sr-only text carries
              // the meaning for anyone the tooltip does not reach.
              title={BYPASS_HINT[provider.app]}
              className="shrink-0 rounded-full bg-red-500/[0.12] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.05em] text-red-700"
            >
              Bypass
              <span className="sr-only"> — {BYPASS_HINT[provider.app]}</span>
            </span>
          ) : null}
          {provider.note ? (
            <span className="truncate text-[12px] tracking-[0.01em] text-[var(--color-tertiary-label)]">
              {provider.note}
            </span>
          ) : null}
          {health ? <HealthBadge result={health} /> : null}
        </div>
        {link ? (
          <button
            type="button"
            onClick={() => handlers.onOpenWebsite(link)}
            className="pointer-events-auto mt-0.5 block max-w-full truncate text-[13px] font-medium text-brand-600 hover:underline"
            title={`Open ${link}`}
          >
            {link}
          </button>
        ) : (
          <span className="mt-0.5 block text-[13px] text-[var(--color-tertiary-label)]">
            No endpoint — uses the vendor sign-in
          </span>
        )}
      </div>

      <div className="relative z-[1] flex items-center gap-1">
        {provider.active ? (
          <span className="mr-1 inline-flex items-center gap-1.5 rounded-full bg-brand-600/10 px-2.5 py-1 text-[11px] font-semibold tracking-[-0.01em] text-brand-700 shadow-[inset_0_0_0_0.5px_rgba(0,122,255,0.25)]">
            <CheckIcon className="h-3.5 w-3.5" />
            In use
          </span>
        ) : null}
        <div className="flex items-center gap-0.5 opacity-0 transition-opacity duration-150 group-focus-within:opacity-100 group-hover:opacity-100">
          <IconButton label="Edit" onClick={() => handlers.onEdit(provider)}>
            <PencilIcon className="h-4 w-4" />
          </IconButton>
          <IconButton label="Duplicate" onClick={() => handlers.onDuplicate(provider)}>
            <CopyIcon className="h-4 w-4" />
          </IconButton>
          <IconButton label="Test connection" onClick={() => handlers.onTest(provider)}>
            <PulseIcon className="h-4 w-4" />
          </IconButton>
          <IconButton
            label="Delete"
            danger
            disabled={provider.active}
            onClick={() => handlers.onDelete(provider)}
          >
            <TrashIcon className="h-4 w-4" />
          </IconButton>
        </div>
      </div>
    </li>
  );
}

export function ProviderList({
  providers,
  health,
  busyId,
  handlers,
  onReorder,
}: {
  providers: ProviderSummary[];
  health: Record<string, HealthResult>;
  busyId: string | null;
  handlers: RowHandlers;
  onReorder: (ids: string[]) => void;
}) {
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const drop = (target: number) => {
    if (dragIndex === null || dragIndex === target) {
      setDragIndex(null);
      return;
    }
    const ordered = providers.map((p) => p.id);
    const [moved] = ordered.splice(dragIndex, 1);
    ordered.splice(target, 0, moved);
    setDragIndex(null);
    onReorder(ordered);
  };

  if (providers.length === 0) {
    return (
      <p className="liquid-glass rounded-[22px] px-6 py-16 text-center text-[14px] text-[var(--color-secondary-label)]">
        No providers yet. Use the + button to add one.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-3">
      {providers.map((provider, index) => (
        <ProviderRow
          key={provider.id}
          provider={provider}
          health={health[provider.id]}
          busy={busyId === provider.id}
          dragging={dragIndex === index}
          handlers={handlers}
          dragProps={{
            draggable: true,
            onDragStart: () => setDragIndex(index),
            onDragEnd: () => setDragIndex(null),
            onDragOver: (event) => event.preventDefault(),
            onDrop: (event) => {
              event.preventDefault();
              drop(index);
            },
          }}
        />
      ))}
    </ul>
  );
}
