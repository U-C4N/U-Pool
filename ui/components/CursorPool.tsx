"use client";

/**
 * The Cursor tab: a pool of session cookies with one of them signed in.
 *
 * It reads its own state instead of taking it from `bootstrap`, because a Cursor
 * account is not a provider and nothing about it belongs in `AppState`. Raising a
 * toast is the one thing it cannot do for itself, so that arrives as a prop.
 */
import { useCallback, useEffect, useState } from "react";
import { backend } from "@/lib/bridge";
import type { CursorAccountSummary, CursorState } from "@/lib/types";
import { type CardHandlers, CursorCard, nameOf } from "./CursorCard";
import { CursorImport, importSummary } from "./CursorImport";
import type { ToastKind } from "./Toast";
import { PlusIcon, RefreshIcon } from "./icons";
import { Button, Modal, Tip, cx } from "./ui";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function CursorPool({
  onToast,
}: {
  onToast: (kind: ToastKind, title: string, detail?: string) => void;
}) {
  const [state, setState] = useState<CursorState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [menuId, setMenuId] = useState<string | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [pendingUse, setPendingUse] = useState<CursorAccountSummary | null>(null);
  const [pendingDelete, setPendingDelete] = useState<CursorAccountSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    backend
      .cursorState()
      .then((next) => !cancelled && setState(next))
      .catch((error) => !cancelled && setLoadError(message(error)));
    return () => {
      cancelled = true;
    };
  }, []);

  // A refresh answers on a background thread, so the result arrives by polling -
  // the same arrangement the CLI version probe uses.
  useEffect(() => {
    if (!state?.busy) return;
    const timer = setInterval(() => {
      backend.cursorState().then(setState).catch(() => undefined);
    }, 500);
    return () => clearInterval(timer);
  }, [state?.busy]);

  const run = useCallback(
    async <T,>(id: string | null, action: () => Promise<T>): Promise<T | null> => {
      setBusyId(id);
      try {
        return await action();
      } catch (error) {
        onToast("error", message(error));
        return null;
      } finally {
        setBusyId(null);
      }
    },
    [onToast],
  );

  /** An empty id is every account, which is what the header button asks for. */
  const refresh = useCallback(
    (id = "") => {
      setMenuId(null);
      backend
        .cursorRefresh(id)
        .then(setState)
        .catch((error) => onToast("error", message(error)));
    },
    [onToast],
  );

  const applyUse = useCallback(
    async (account: CursorAccountSummary) => {
      setPendingUse(null);
      const result = await run(account.id, () => backend.cursorUse(account.id));
      if (!result) return;
      setState(result.state);
      if (result.warnings.length > 0) {
        onToast("error", `Switched to ${nameOf(account)}`, result.warnings.join(" "));
        return;
      }
      const movement =
        result.closed_cursor && result.relaunched
          ? "Cursor was closed and opened again."
          : result.relaunched
            ? "Cursor is starting."
            : "Start Cursor to pick the account up.";
      onToast(
        "success",
        `${nameOf(account)} is now in use`,
        [movement, result.files.join("  •  ")].filter(Boolean).join(" "),
      );
    },
    [onToast, run],
  );

  const confirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    const account = pendingDelete;
    setPendingDelete(null);
    const next = await run(account.id, () => backend.cursorDelete(account.id));
    if (!next) return;
    setState(next);
    onToast("success", `Removed ${nameOf(account)}`);
  }, [onToast, pendingDelete, run]);

  const submitImport = useCallback(
    async (text: string) => {
      setImporting(true);
      setImportError(null);
      try {
        const result = await backend.cursorAdd(text);
        setState(result.state);
        // Everything was skipped, so the modal stays open with what it found -
        // closing it would leave the user guessing at their own paste.
        if (result.added + result.refreshed === 0) {
          setImportError(
            `Nothing in that paste looked like a Cursor cookie${
              result.skipped > 0 ? ` — ${result.skipped} lines skipped` : ""
            }.`,
          );
          return;
        }
        setImportOpen(false);
        onToast("success", importSummary(result), "Reading the plan and usage for each one…");
        // A new row carries no plan and no usage, so the import ends by asking
        // for them - that is what the shimmer on an unchecked card waits on.
        refresh();
      } catch (error) {
        setImportError(message(error));
      } finally {
        setImporting(false);
      }
    },
    [onToast, refresh],
  );

  const drop = (target: number) => {
    if (!state || dragIndex === null || dragIndex === target) {
      setDragIndex(null);
      return;
    }
    const ordered = state.accounts.map((account) => account.id);
    const [moved] = ordered.splice(dragIndex, 1);
    ordered.splice(target, 0, moved);
    setDragIndex(null);
    backend
      .cursorReorder(ordered)
      .then(setState)
      .catch((error) => onToast("error", message(error)));
  };

  const accounts = state?.accounts ?? [];
  const busy = Boolean(state?.busy);
  const handlers: CardHandlers = {
    // Closing an editor is worth a confirmation; a switch with Cursor already
    // shut is not.
    onUse: (account) => (state?.running ? setPendingUse(account) : applyUse(account)),
    onRefresh: (account) => refresh(account.id),
    onDelete: (account) => {
      setMenuId(null);
      setPendingDelete(account);
    },
  };

  return (
    <div className="animate-fade-in mx-auto w-full max-w-[720px] px-5 py-6">
      <div className="mb-5 flex items-end justify-between gap-4 px-1">
        <div className="min-w-0">
          <h1 className="display-title text-[var(--color-label)]">Cursor</h1>
          <p className="mt-2 text-[13px] leading-snug tracking-[-0.01em] text-[var(--color-secondary-label)]">
            Use signs Cursor in as that account. The editor is closed first and opened again after.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button onClick={() => refresh()} disabled={busy || accounts.length === 0}>
            <RefreshIcon className={cx("h-4 w-4", busy && "animate-spin")} />
            {busy ? "Refreshing…" : "Refresh all"}
          </Button>
          <Button variant="primary" onClick={() => setImportOpen(true)}>
            <PlusIcon className="h-4 w-4" />
            Add account
          </Button>
        </div>
      </div>

      {state && !state.supported ? (
        <div className="mb-3 px-1">
          <Tip>
            Cursor was not found on this machine — U-Pool looked for{" "}
            {state.db_path || "its state database"}. Accounts can still be kept here, but switching
            needs Cursor installed.
          </Tip>
        </div>
      ) : null}

      {loadError ? (
        <p className="liquid-glass rounded-[22px] px-6 py-10 text-center text-[14px] text-red-600">
          {loadError}
        </p>
      ) : !state ? (
        <div className="liquid-glass h-28 animate-pulse rounded-[22px]" />
      ) : accounts.length === 0 ? (
        <div className="liquid-glass rounded-[22px] px-6 py-12 text-center">
          <p className="relative z-[1] text-[15px] font-semibold tracking-[-0.02em] text-[var(--color-label)]">
            No Cursor accounts yet
          </p>
          <p className="relative z-[1] mx-auto mt-2 max-w-[420px] text-[13px] leading-relaxed text-[var(--color-secondary-label)]">
            Sign in at cursor.com, open DevTools › Application › Cookies and copy the value of{" "}
            <span className="font-mono text-[12px]">WorkosCursorSessionToken</span>. A cookies.txt
            export from a cookie extension works just as well.
          </p>
          <span className="relative z-[1] mt-4 inline-flex">
            <Button variant="primary" onClick={() => setImportOpen(true)}>
              <PlusIcon className="h-4 w-4" />
              Add account
            </Button>
          </span>
        </div>
      ) : (
        <ul className="flex flex-col gap-3">
          {accounts.map((account, index) => (
            <CursorCard
              key={account.id}
              account={account}
              poolBusy={busy}
              busy={busyId === account.id}
              supported={state.supported}
              menuOpen={menuId === account.id}
              dragging={dragIndex === index}
              onMenu={(open) => setMenuId(open ? account.id : null)}
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
      )}

      {importOpen ? (
        <CursorImport
          submitting={importing}
          error={importError}
          onSubmit={submitImport}
          onClose={() => {
            setImportOpen(false);
            setImportError(null);
          }}
        />
      ) : null}

      {pendingUse ? (
        <Modal
          title="Close Cursor to switch?"
          onClose={() => setPendingUse(null)}
          footer={
            <>
              <Button onClick={() => setPendingUse(null)}>Cancel</Button>
              <Button variant="primary" onClick={() => applyUse(pendingUse)}>
                Close and switch
              </Button>
            </>
          }
        >
          Cursor is running. U-Pool asks it to close, writes {nameOf(pendingUse)} into its database
          and starts it again. It never force-quits: if Cursor stays open, nothing is written and
          your unsaved work is untouched.
        </Modal>
      ) : null}

      {pendingDelete ? (
        <Modal
          title={`Remove ${nameOf(pendingDelete)}?`}
          onClose={() => setPendingDelete(null)}
          footer={
            <>
              <Button onClick={() => setPendingDelete(null)}>Cancel</Button>
              <Button variant="danger" onClick={confirmDelete}>
                Remove
              </Button>
            </>
          }
        >
          This drops the stored session cookie from U-Pool&apos;s pool. Cursor stays signed in to
          whoever it is signed in as right now, and pasting the cookie again brings the account
          back.
        </Modal>
      ) : null}
    </div>
  );
}
