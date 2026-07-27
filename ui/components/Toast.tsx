"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertIcon, CheckCircleIcon, XIcon } from "./icons";

export type ToastKind = "success" | "error" | "info";

export interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  detail?: string;
}

const LIFETIME_MS = 4000;

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (kind: ToastKind, title: string, detail?: string) => {
      const id = nextId.current++;
      setToasts((current) => [...current.slice(-2), { id, kind, title, detail }]);
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), LIFETIME_MS),
      );
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach(clearTimeout);
  }, []);

  return { toasts, push, dismiss };
}

const TONE: Record<ToastKind, { ring: string; icon: string }> = {
  success: { ring: "ring-emerald-100", icon: "text-emerald-500" },
  error: { ring: "ring-red-100", icon: "text-red-500" },
  info: { ring: "ring-zinc-100", icon: "text-zinc-400" },
};

export function ToastStack({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="pointer-events-none fixed inset-x-0 top-4 z-50 flex flex-col items-center gap-2">
      {toasts.map((toast) => {
        const tone = TONE[toast.kind];
        return (
          <div
            key={toast.id}
            role="status"
            className={`animate-toast-in liquid-glass-strong pointer-events-auto flex max-w-lg items-start gap-2.5 rounded-[18px] px-4 py-3 ring-1 ${tone.ring}`}
          >
            {toast.kind === "success" ? (
              <CheckCircleIcon className={`mt-px h-5 w-5 shrink-0 ${tone.icon}`} />
            ) : (
              <AlertIcon className={`mt-px h-5 w-5 shrink-0 ${tone.icon}`} />
            )}
            <div className="min-w-0">
              <p className="text-sm font-medium text-zinc-800">{toast.title}</p>
              {toast.detail ? <p className="mt-0.5 text-xs text-zinc-500">{toast.detail}</p> : null}
            </div>
            <button
              type="button"
              onClick={() => onDismiss(toast.id)}
              aria-label="Dismiss"
              className="-mr-1.5 -mt-1 rounded-md p-1 text-zinc-300 transition hover:bg-zinc-100 hover:text-zinc-500"
            >
              <XIcon className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
