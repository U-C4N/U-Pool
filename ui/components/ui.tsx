"use client";

/** Small shared primitives: buttons, form fields, modal shell. */
import { useEffect, useId, useRef, useState } from "react";
import { EyeIcon, EyeOffIcon, XIcon } from "./icons";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function IconButton({
  label,
  active,
  danger,
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  active?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      className={cx(
        "pressable grid h-8 w-8 place-items-center rounded-full text-[var(--color-secondary-label)]",
        "hover:bg-white/55 hover:text-[var(--color-label)]",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent",
        active && "bg-white/70 text-[var(--color-label)]",
        danger && "hover:bg-red-50/80 hover:text-red-600",
        className,
      )}
      {...props}
    />
  );
}

export function Button({
  variant = "secondary",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  const tone = {
    primary:
      "bg-brand-600 text-white shadow-[0_10px_24px_-10px_rgba(0,122,255,0.75),inset_0_1px_0_rgba(255,255,255,0.3)] hover:bg-brand-500",
    secondary: "liquid-pill text-[var(--color-label)] hover:bg-white/55",
    danger: "bg-red-500 text-white shadow-[0_10px_24px_-10px_rgba(239,68,68,0.55)] hover:bg-red-600",
  }[variant];
  return (
    <button
      type="button"
      className={cx(
        "pressable inline-flex h-9 items-center justify-center gap-2 rounded-full px-4 text-[13px] font-semibold tracking-[-0.02em]",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
        "disabled:cursor-not-allowed disabled:opacity-50",
        tone,
        className,
      )}
      {...props}
    />
  );
}

const FIELD_CLASS =
  "h-11 w-full rounded-[14px] bg-white/45 px-3.5 text-[15px] tracking-[-0.015em] text-[var(--color-label)] transition " +
  "placeholder:text-[var(--color-tertiary-label)] hover:bg-white/60 " +
  "focus:bg-white/85 focus:shadow-[0_0_0_3.5px_rgba(0,122,255,0.16),0_0_0_1px_rgba(0,122,255,0.4)] focus:outline-none";

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[13px] font-medium text-[var(--color-secondary-label)]">
        {label}
      </span>
      {children}
      {hint ? (
        <span className="mt-1.5 block text-[12px] leading-relaxed text-[var(--color-tertiary-label)]">
          {hint}
        </span>
      ) : null}
    </label>
  );
}

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(FIELD_CLASS, props.className)} />;
}

export function Select({
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement> & { children: React.ReactNode }) {
  return (
    <select {...props} className={cx(FIELD_CLASS, "pr-8", props.className)}>
      {children}
    </select>
  );
}

export function SecretInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  const [revealed, setRevealed] = useState(false);
  return (
    <div className="relative">
      <input {...props} type={revealed ? "text" : "password"} className={cx(FIELD_CLASS, "pr-11")} />
      <button
        type="button"
        onClick={() => setRevealed((v) => !v)}
        aria-label={revealed ? "Hide API key" : "Show API key"}
        className="absolute inset-y-0 right-0 grid w-11 place-items-center rounded-r-[10px] text-[var(--color-tertiary-label)] transition hover:text-[var(--color-label)]"
      >
        {revealed ? <EyeOffIcon className="h-4 w-4" /> : <EyeIcon className="h-4 w-4" />}
      </button>
    </div>
  );
}

export function Tip({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-[14px] bg-amber-100/55 px-3.5 py-2.5 text-[12px] leading-relaxed tracking-[-0.01em] text-amber-950/75 shadow-[inset_0_0_0_0.5px_rgba(255,255,255,0.55)]">
      {children}
    </div>
  );
}

/** Centred modal with focus trapping kept intentionally simple: Esc closes. */
export function Modal({
  title,
  children,
  footer,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  onClose: () => void;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    panel.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-slate-900/20 p-6 backdrop-blur-[8px]">
      <div
        ref={panel}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="animate-sheet-in liquid-glass-strong relative w-full max-w-md rounded-[24px] p-5 outline-none"
      >
        <div className="flex items-start justify-between gap-4">
          <h2 id={titleId} className="text-[17px] font-semibold tracking-[-0.02em] text-[var(--color-label)]">
            {title}
          </h2>
          <IconButton label="Close" className="h-7 w-7" onClick={onClose}>
            <XIcon className="h-4 w-4" />
          </IconButton>
        </div>
        <div className="mt-3 text-[14px] leading-relaxed text-[var(--color-secondary-label)]">{children}</div>
        {footer ? <div className="mt-5 flex justify-end gap-2">{footer}</div> : null}
      </div>
    </div>
  );
}
