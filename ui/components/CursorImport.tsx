"use client";

/**
 * One box for every way a Cursor cookie arrives.
 *
 * Bulk and single are the same path on purpose - a separate "import many" screen
 * would be a second implementation of one function - so a single line, a two
 * hundred line `cookies.txt` and a JSON dump all go into the same textarea, and
 * the file picker only fills that same box.
 */
import { useEffect, useRef, useState } from "react";
import type { CursorAddResult } from "@/lib/types";
import { FileIcon } from "./icons";
import { Button, Field, Modal, cx } from "./ui";

/** The accepted shapes, shown rather than described. */
const PLACEHOLDER = `WorkosCursorSessionToken=user_01AB%3A%3AeyJhbGciOiJIUzI1...
user_01AB::eyJhbGciOiJIUzI1...
you@example.com,user_01AB::eyJhbGciOiJIUzI1...

…or paste a whole cookies.txt export, or JSON with a token field.`;

// Mirrors the field styling in ui.tsx, whose FIELD_CLASS is private to that
// module and sized for a one-line input.
const BOX_CLASS =
  "block h-44 w-full resize-none rounded-[14px] bg-white/45 p-3 font-mono text-[11.5px] leading-relaxed " +
  "tracking-normal text-[var(--color-label)] transition placeholder:text-[var(--color-tertiary-label)] " +
  "hover:bg-white/60 focus:bg-white/85 focus:outline-none " +
  "focus:shadow-[0_0_0_3.5px_rgba(0,122,255,0.16),0_0_0_1px_rgba(0,122,255,0.4)]";

/**
 * The whole report of a paste, in one line.
 *
 * Counts only: a browser's cookies.txt holds comment lines and every other
 * domain's cookies, so naming each skipped line is noise rather than detail.
 */
export function importSummary(result: CursorAddResult): string {
  const parts = [
    result.added > 0 ? `${result.added} added` : "",
    result.refreshed > 0 ? `${result.refreshed} refreshed` : "",
    result.skipped > 0
      ? `${result.skipped} ${result.skipped === 1 ? "line" : "lines"} skipped`
      : "",
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(", ") : "Nothing to add";
}

export function CursorImport({
  submitting,
  error,
  onSubmit,
  onClose,
}: {
  submitting: boolean;
  /** What the backend made of the last paste, when it made nothing of it. */
  error: string | null;
  onSubmit: (text: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [emptyBox, setEmptyBox] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  // `autoFocus` loses: Modal focuses its own panel from an effect that runs
  // after this one, and the whole sheet is a single paste box.
  useEffect(() => {
    const timer = setTimeout(() => box.current?.focus(), 0);
    return () => clearTimeout(timer);
  }, []);

  const lines = text.split("\n").filter((line) => line.trim()).length;

  const readFile = async (file: File | undefined) => {
    if (!file) return;
    const content = await file.text();
    // Appended, not swapped in: picking a file after pasting a cookie must not
    // throw the cookie away.
    setText((current) => (current.trim() ? `${current.trimEnd()}\n${content}` : content));
    setEmptyBox(false);
  };

  const submit = () => {
    if (!text.trim()) {
      setEmptyBox(true);
      return;
    }
    onSubmit(text);
  };

  const problem = emptyBox ? "Paste a cookie, or choose a file, first." : error;

  return (
    <Modal
      title="Add Cursor accounts"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={submit} disabled={submitting}>
            {submitting ? "Adding…" : "Add accounts"}
          </Button>
        </>
      }
    >
      <Field
        label="Session cookies"
        hint="Anything that is not a Cursor cookie is ignored, so a whole export is fine. A cookie for an account already in the pool refreshes it in place."
      >
        <textarea
          ref={box}
          value={text}
          spellCheck={false}
          placeholder={PLACEHOLDER}
          onChange={(event) => {
            setText(event.target.value);
            setEmptyBox(false);
          }}
          className={BOX_CLASS}
        />
      </Field>

      <div className="mt-3 flex items-center justify-between gap-3">
        <Button className="h-9 px-3" onClick={() => picker.current?.click()}>
          <FileIcon className="h-4 w-4" />
          Choose file…
        </Button>
        <span className="text-[12px] text-[var(--color-tertiary-label)]">
          {lines > 0 ? `${lines} ${lines === 1 ? "line" : "lines"}` : "cookies.txt, csv or json"}
        </span>
        <input
          ref={picker}
          type="file"
          accept=".txt,.csv,.json"
          className="hidden"
          onChange={(event) => {
            void readFile(event.target.files?.[0]);
            // Cleared so picking the same file twice still fires a change.
            event.target.value = "";
          }}
        />
      </div>

      <p
        className={cx(
          "mt-3 text-[12px] leading-relaxed",
          problem ? "text-red-600" : "text-[var(--color-tertiary-label)]",
        )}
      >
        {problem ??
          "In Cursor's web dashboard the cookie is WorkosCursorSessionToken, under DevTools › Application › Cookies."}
      </p>
    </Modal>
  );
}
