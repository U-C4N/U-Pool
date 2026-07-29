"use client";

/** Small shared primitives: buttons, form fields, modal shell. */
import { useEffect, useId, useRef, useState } from "react";
import { CheckIcon, EyeIcon, EyeOffIcon, XIcon } from "./icons";

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
        "pressable grid h-8 w-8 place-items-center rounded-[10px] text-[var(--color-secondary-label)]",
        "hover:bg-black/[0.05] hover:text-[var(--color-label)]",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent",
        active && "bg-black/[0.06] text-[var(--color-label)]",
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

/** Checkbox row: the label is the hit target, the hint explains the cost. */
export function Checkbox({
  label,
  hint,
  checked,
  disabled,
  danger,
  onChange,
}: {
  label: string;
  hint?: React.ReactNode;
  checked: boolean;
  disabled?: boolean;
  danger?: boolean;
  onChange: (checked: boolean) => void;
}) {
  // The hint names the config key this box writes: description, not label, or a
  // screen reader announces a 20-word name for a checkbox.
  const nameId = useId();
  const hintId = useId();
  return (
    <label
      className={cx(
        "flex items-start gap-2.5 rounded-[14px] px-2.5 py-2 transition",
        // Both cursor utilities set the same property, so only one may be emitted.
        disabled ? "cursor-not-allowed opacity-45" : "cursor-pointer hover:bg-white/45",
        checked && danger && !disabled && "bg-red-500/[0.07]",
      )}
    >
      <span className="relative mt-[3px] grid h-[17px] w-[17px] shrink-0 place-items-center">
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          aria-labelledby={nameId}
          aria-describedby={hint ? hintId : undefined}
          onChange={(event) => onChange(event.target.checked)}
          className={cx(
            "peer h-full w-full appearance-none rounded-[5px] bg-white/70 transition",
            "shadow-[inset_0_0_0_1px_rgba(15,23,42,0.18)] checked:shadow-none",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
            disabled ? "cursor-not-allowed" : "cursor-pointer",
            danger ? "checked:bg-red-500" : "checked:bg-brand-600",
          )}
        />
        <CheckIcon
          strokeWidth={3.2}
          className="pointer-events-none absolute h-3 w-3 text-white opacity-0 peer-checked:opacity-100"
        />
      </span>
      <span className="min-w-0">
        <span
          id={nameId}
          className={cx(
            "block text-[13px] font-medium tracking-[-0.01em]",
            danger && checked && !disabled ? "text-red-700" : "text-[var(--color-label)]",
          )}
        >
          {label}
        </span>
        {hint ? (
          <span
            id={hintId}
            className="mt-0.5 block text-[12px] leading-relaxed text-[var(--color-tertiary-label)]"
          >
            {hint}
          </span>
        ) : null}
      </span>
    </label>
  );
}

/** iOS-style switch for a single app-level preference. */
export function Switch({
  label,
  checked,
  disabled,
  busy,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  busy?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      // Only a permanently unavailable switch is `disabled`. An in-flight write uses
      // aria-busy instead, because disabling a focused button drops keyboard focus.
      disabled={disabled}
      aria-busy={busy || undefined}
      onClick={() => !busy && onChange(!checked)}
      className={cx(
        "relative h-[26px] w-[44px] shrink-0 rounded-full transition-colors duration-200",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
        checked ? "bg-brand-600" : "bg-black/[0.14]",
        disabled && "cursor-not-allowed opacity-45",
        busy && "cursor-progress opacity-70",
      )}
    >
      <span
        aria-hidden
        className={cx(
          "absolute top-[3px] h-5 w-5 rounded-full bg-white shadow-[0_2px_6px_-1px_rgba(15,23,42,0.45)] transition-[left] duration-200",
          checked ? "left-[21px]" : "left-[3px]",
          busy && "animate-pulse",
        )}
      />
    </button>
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
