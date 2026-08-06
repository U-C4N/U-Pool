"use client";

/**
 * One pooled Cursor account.
 *
 * Split out of `CursorPool` rather than nested in it: the list is a handful of
 * calls and a modal or two, while the card is where every undocumented usage
 * shape has to land softly, and the two read better apart.
 */
import type { CursorAccountSummary, CursorStatus, CursorUsageUnit } from "@/lib/types";
import { CheckIcon, EllipsisIcon, GripIcon, RefreshIcon, TrashIcon } from "./icons";
import { Button, IconButton, cx } from "./ui";

const DASH = "—";

const STATUS_TONE: Record<CursorStatus, string> = {
  ok: "bg-emerald-500",
  expired: "bg-red-500",
  unknown: "bg-zinc-300",
};

/** An account is known by whichever of these the last refresh actually reached. */
export function nameOf(account: CursorAccountSummary): string {
  return account.name || account.email || account.user_id;
}

function figure(value: number, unit: CursorUsageUnit): string {
  return unit === "usd" ? `$${value.toFixed(2)}` : value.toLocaleString();
}

/** "$12.40 / $20.00", "312 / 500 requests", or an em dash for a figure that never came. */
function usageLabel(account: CursorAccountSummary): string {
  const { usage_used: used, usage_limit: limit, usage_unit: unit } = account;
  if (used === null && limit === null) return DASH;
  const left = used === null ? DASH : figure(used, unit);
  const right = limit === null ? DASH : figure(limit, unit);
  return `${left} / ${right}${unit === "requests" ? " requests" : ""}`;
}

/**
 * How full to draw the bar, or null to leave it empty.
 *
 * The percentage the backend computed wins; the division is a fallback for a
 * response that carried both figures and no percentage. Anything that cannot be
 * divided stays null, because a card reading 0% of an unknown quota is a lie and
 * these endpoints are undocumented enough to produce one.
 */
function usageFraction(account: CursorAccountSummary): number | null {
  const { usage_percent: percent, usage_used: used, usage_limit: limit } = account;
  if (percent !== null && Number.isFinite(percent)) return percent;
  if (used === null || limit === null) return null;
  if (!Number.isFinite(used) || !Number.isFinite(limit) || limit <= 0) return null;
  return (used / limit) * 100;
}

/** Green until the quota is in sight, then amber, then red. */
function barTone(percent: number): string {
  if (percent >= 90) return "bg-red-500";
  if (percent >= 70) return "bg-amber-500";
  return "bg-emerald-500";
}

function checkedAgo(at: number): string {
  if (!at) return "not checked yet";
  const minutes = Math.round((Date.now() - at) / 60_000);
  if (minutes < 1) return "refreshed just now";
  if (minutes < 60) return `refreshed ${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `refreshed ${hours}h ago` : `refreshed ${Math.round(hours / 24)}d ago`;
}

function Badge({
  tone = "plain",
  title,
  children,
}: {
  tone?: "plain" | "active" | "expired";
  title?: string;
  children: React.ReactNode;
}) {
  const style = {
    plain: "bg-white/50 text-[var(--color-secondary-label)]",
    active: "bg-brand-600/10 text-brand-700",
    expired: "bg-red-500/[0.12] text-red-700",
  }[tone];
  return (
    <span
      title={title}
      className={cx(
        "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.05em]",
        style,
      )}
    >
      {children}
    </span>
  );
}

function MenuItem({
  icon,
  label,
  danger,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={cx(
        "flex w-full items-center gap-2 rounded-[10px] px-2.5 py-2 text-left text-[13px] font-medium",
        danger ? "text-red-600 hover:bg-red-50/80" : "text-[var(--color-label)] hover:bg-black/[0.05]",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

export interface CardHandlers {
  onUse: (account: CursorAccountSummary) => void;
  onRefresh: (account: CursorAccountSummary) => void;
  onDelete: (account: CursorAccountSummary) => void;
}

export function CursorCard({
  account,
  poolBusy,
  busy,
  supported,
  menuOpen,
  dragging,
  onMenu,
  handlers,
  dragProps,
}: {
  account: CursorAccountSummary;
  /** A refresh is running somewhere in the pool. */
  poolBusy: boolean;
  /** This account is the one being switched to or removed. */
  busy: boolean;
  supported: boolean;
  menuOpen: boolean;
  dragging: boolean;
  onMenu: (open: boolean) => void;
  handlers: CardHandlers;
  dragProps: React.HTMLAttributes<HTMLLIElement> & { draggable?: boolean };
}) {
  const expired = account.status === "expired";
  const percent = usageFraction(account);
  // Nothing has ever answered for this row and a refresh is running, so it
  // shimmers rather than reading as a real zero.
  const pending = poolBusy && account.last_checked === 0;
  // Mirror switch._ensure_session_token: a browser cookie is now converted on
  // Use, not refused, so it no longer blocks. Only a truly dead row does - an
  // expired session with no cookie behind it to re-mint from.
  const blocked = !supported
    ? "U-Pool could not find Cursor on this machine."
    : !account.has_token
      ? "No session cookie is stored for this account."
      : expired && account.token_kind === "session" && !account.has_web_token
        ? "This account's session has expired — paste a fresh cookie for it."
        : "";

  return (
    <li
      {...dragProps}
      className={cx(
        "liquid-glass group relative rounded-[20px] px-4 py-3.5 transition-[transform,opacity] duration-200",
        account.active &&
          "shadow-[0_0_0_1px_rgba(0,122,255,0.22),0_18px_40px_-24px_rgba(0,122,255,0.55)]",
        expired && "opacity-65",
        // Every card is its own stacking context, so an open menu has to lift the
        // whole card or the cards below it paint over the popover.
        menuOpen && "z-30",
        dragging && "scale-[0.98] opacity-40",
        busy && "pointer-events-none opacity-60",
      )}
    >
      <div className="relative z-[1]">
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className={cx("h-2 w-2 shrink-0 rounded-full", STATUS_TONE[account.status])}
          />
          <span className="truncate text-[15px] font-semibold tracking-[-0.025em] text-[var(--color-label)]">
            {nameOf(account)}
          </span>
          {account.plan ? (
            <Badge title={account.plan_status ? `Subscription ${account.plan_status}` : undefined}>
              {account.plan}
            </Badge>
          ) : null}
          {account.active ? <Badge tone="active">Active</Badge> : null}
          {expired ? <Badge tone="expired">Expired</Badge> : null}
          <span className="flex-1" />
          <span
            title="Drag to reorder"
            className="cursor-grab text-[var(--color-tertiary-label)] opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 active:cursor-grabbing"
          >
            <GripIcon className="h-4 w-4" />
          </span>
          <IconButton
            label={`More for ${nameOf(account)}`}
            active={menuOpen}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            className="h-7 w-7"
            onClick={() => onMenu(!menuOpen)}
          >
            <EllipsisIcon className="h-4 w-4" />
          </IconButton>
        </div>

        <p
          className="mt-0.5 truncate pl-4 text-[13px] text-[var(--color-secondary-label)]"
          title={account.user_id}
        >
          {account.email || account.user_id}
        </p>

        {pending ? (
          <div className="ml-4 mt-3 h-1.5 animate-pulse rounded-full bg-black/[0.07]" />
        ) : (
          <div className="mt-3 flex items-center gap-3 pl-4">
            <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-black/[0.07]">
              {percent === null ? null : (
                <span
                  className={cx(
                    "block h-full rounded-full shadow-[inset_0_1px_0_rgba(255,255,255,0.45)] transition-[width] duration-500",
                    barTone(percent),
                  )}
                  style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
                />
              )}
            </span>
            <span className="shrink-0 text-[12px] font-semibold tabular-nums text-[var(--color-secondary-label)]">
              {percent === null ? DASH : `${Math.round(percent)}%`}
            </span>
            <span className="shrink-0 text-[12px] tabular-nums text-[var(--color-secondary-label)]">
              {usageLabel(account)}
            </span>
          </div>
        )}

        <div className="mt-3 flex items-center justify-between gap-3 pl-4">
          <span className="truncate text-[12px] text-[var(--color-tertiary-label)]">
            {pending ? "checking…" : checkedAgo(account.last_checked)}
          </span>
          {account.active ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-600/10 px-2.5 py-1 text-[11px] font-semibold tracking-[-0.01em] text-brand-700 shadow-[inset_0_0_0_0.5px_rgba(0,122,255,0.25)]">
              <CheckIcon className="h-3.5 w-3.5" />
              In use
            </span>
          ) : (
            <Button
              variant="primary"
              className="h-8 px-5"
              disabled={Boolean(blocked)}
              title={blocked || `Sign Cursor in as ${nameOf(account)}`}
              onClick={() => handlers.onUse(account)}
            >
              Use
            </Button>
          )}
        </div>
      </div>

      {menuOpen ? (
        <>
          <button
            type="button"
            aria-label="Close menu"
            className="fixed inset-0 z-10 cursor-default"
            onClick={() => onMenu(false)}
          />
          <div
            role="menu"
            className="liquid-glass-strong absolute right-3 top-11 z-20 w-44 rounded-[14px] p-1"
          >
            <MenuItem
              icon={<RefreshIcon className="h-4 w-4" />}
              label="Refresh this one"
              onClick={() => handlers.onRefresh(account)}
            />
            <MenuItem
              danger
              icon={<TrashIcon className="h-4 w-4" />}
              label="Remove"
              onClick={() => handlers.onDelete(account)}
            />
          </div>
        </>
      ) : null}
    </li>
  );
}
