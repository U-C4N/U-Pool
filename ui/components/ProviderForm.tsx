"use client";

import { useMemo, useState } from "react";
import { type ProviderPreset, presetsFor } from "@/lib/presets";
import type { AppId, ProviderDetail } from "@/lib/types";
import { PresetIcon } from "./BrandMarks";
import { ArrowLeftIcon, ChevronIcon, PlusIcon, XIcon } from "./icons";
import { Button, Field, IconButton, SecretInput, Select, TextInput, Tip, cx } from "./ui";

type Draft = Omit<ProviderDetail, "created_at" | "updated_at">;

function blank(app: AppId): Draft {
  return {
    id: "",
    app,
    name: "",
    note: "",
    website: "",
    base_url: "",
    api_key: "",
    model: "",
    auth_style: "auth_token",
    small_fast_model: "",
    wire_api: "responses",
    env_key: "OPENAI_API_KEY",
    extra: {},
    official: false,
  };
}

function draftFromPreset(app: AppId, preset: ProviderPreset, apiKey = ""): Draft {
  if (preset.id === "custom") return { ...blank(app), api_key: apiKey };
  return {
    ...blank(app),
    name: preset.name,
    note: preset.note ?? "",
    website: preset.website,
    base_url: preset.base_url,
    api_key: apiKey,
    model: preset.model ?? "",
    small_fast_model: preset.small_fast_model ?? "",
    auth_style: preset.auth_style ?? "auth_token",
    wire_api: preset.wire_api ?? "responses",
    env_key: preset.env_key ?? "OPENAI_API_KEY",
    extra: { ...(preset.extra ?? {}) },
  };
}

const URL_HINT: Record<AppId, string> = {
  claude:
    "Any Claude-compatible endpoint. Enter the base address without a trailing slash - U-Pool appends the API paths.",
  codex:
    "OpenAI-compatible endpoint, usually ending in /v1. Pick the wire API below if the relay does not speak the Responses API.",
};

/** Key/value rows for the escape-hatch fields each adapter passes straight through. */
function ExtraEditor({
  entries,
  onChange,
  keyLabel,
}: {
  entries: [string, string][];
  onChange: (entries: [string, string][]) => void;
  keyLabel: string;
}) {
  return (
    <div className="space-y-2">
      {entries.map(([key, value], index) => (
        <div key={index} className="flex items-center gap-2">
          <TextInput
            value={key}
            placeholder={keyLabel}
            onChange={(event) => {
              const next = [...entries];
              next[index] = [event.target.value, value];
              onChange(next);
            }}
          />
          <TextInput
            value={value}
            placeholder="value"
            onChange={(event) => {
              const next = [...entries];
              next[index] = [key, event.target.value];
              onChange(next);
            }}
          />
          <IconButton
            label="Remove"
            danger
            onClick={() => onChange(entries.filter((_, i) => i !== index))}
          >
            <XIcon className="h-4 w-4" />
          </IconButton>
        </div>
      ))}
      <Button onClick={() => onChange([...entries, ["", ""]])} className="h-9 px-3 text-xs">
        <PlusIcon className="h-4 w-4" />
        Add row
      </Button>
    </div>
  );
}

export function ProviderForm({
  app,
  appLabel,
  initial,
  saving,
  onCancel,
  onSave,
}: {
  app: AppId;
  appLabel: string;
  initial: ProviderDetail | null;
  saving: boolean;
  onCancel: () => void;
  onSave: (draft: Draft) => void;
}) {
  const catalog = useMemo(() => presetsFor(app), [app]);
  const defaultPresetId = "custom";
  const [draft, setDraft] = useState<Draft>(() =>
    initial
      ? { ...initial }
      : draftFromPreset(app, catalog.find((p) => p.id === defaultPresetId) ?? catalog[0]),
  );
  const [presetId, setPresetId] = useState(defaultPresetId);
  const [extra, setExtra] = useState<[string, string][]>(() =>
    Object.entries(initial?.extra ?? {}),
  );
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const editing = Boolean(initial?.id);
  const locked = Boolean(initial?.official);
  const activePreset = catalog.find((p) => p.id === presetId);
  const initial_letter = draft.name.trim().charAt(0).toUpperCase() || "?";

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  const applyPreset = (preset: ProviderPreset) => {
    setPresetId(preset.id);
    setError(null);
    setExtra(Object.entries(preset.extra ?? {}));
    // Keep a key the user already typed when hopping between presets.
    setDraft((current) => draftFromPreset(app, preset, current.api_key));
  };

  const extraMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const [key, value] of extra) {
      if (key.trim()) map[key.trim()] = value;
    }
    return map;
  }, [extra]);

  const submit = () => {
    if (!draft.name.trim()) return setError("Give the provider a name.");
    if (!locked) {
      if (!draft.base_url.trim()) return setError("The request URL is required.");
      if (!/^https?:\/\//i.test(draft.base_url.trim())) {
        return setError("The request URL must start with http:// or https://.");
      }
    }
    setError(null);
    onSave({
      ...draft,
      name: draft.name.trim(),
      base_url: draft.base_url.trim(),
      website: draft.website.trim(),
      extra: extraMap,
    });
  };

  return (
    <div className="animate-fade-in mx-auto w-full max-w-[720px] px-5 pb-28 pt-5">
      <div className="flex items-center gap-2.5">
        <IconButton label="Back" onClick={onCancel}>
          <ArrowLeftIcon className="h-5 w-5" />
        </IconButton>
        <h1 className="text-[22px] font-semibold tracking-[-0.03em] text-[var(--color-label)]">
          {editing ? "Edit provider" : "Add provider"}
        </h1>
        <span className="rounded-full bg-[var(--color-fill)] px-2.5 py-1 text-[11px] font-semibold text-[var(--color-secondary-label)]">
          {appLabel}
        </span>
      </div>

      {!editing ? (
        <div className="liquid-glass relative mt-5 rounded-[22px] p-3">
          <div className="relative z-[1]">
            <div className="mb-2 flex items-baseline justify-between px-1">
              <p className="text-[12px] font-semibold uppercase tracking-[0.06em] text-[var(--color-secondary-label)]">
                Provider preset
              </p>
              <p className="text-[11px] text-[var(--color-tertiary-label)]">
                CC Switch style · {catalog.length} templates
              </p>
            </div>
            <div className="flex max-h-[168px] flex-wrap gap-1.5 overflow-y-auto pr-0.5">
              {catalog.map((preset) => {
                const active = preset.id === presetId;
                return (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() => applyPreset(preset)}
                    className={cx(
                      "pressable inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-[12px] font-semibold tracking-[-0.015em]",
                      active
                        ? "bg-white text-[var(--color-label)] shadow-[0_4px_14px_-6px_rgba(15,23,42,0.35),inset_0_0_0_1px_rgba(0,122,255,0.28)]"
                        : "bg-white/35 text-[var(--color-secondary-label)] hover:bg-white/55 hover:text-[var(--color-label)]",
                    )}
                  >
                    <span style={{ color: preset.tint ?? "currentColor" }}>
                      <PresetIcon
                        presetId={preset.id}
                        name={preset.name}
                        className="h-4 w-4"
                      />
                    </span>
                    {preset.name}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}

      <div className="liquid-glass relative mt-4 rounded-[24px] p-6">
        <div className="relative z-[1]">
        <div className="mb-6 flex justify-center">
          <span className="grid h-16 w-16 place-items-center rounded-[20px] bg-white/55 shadow-[inset_0_1px_0_rgba(255,255,255,0.85),0_10px_24px_-14px_rgba(15,23,42,0.4)]">
            {!editing && activePreset ? (
              <span style={{ color: activePreset.tint ?? "var(--color-label)" }}>
                <PresetIcon
                  presetId={activePreset.id}
                  name={activePreset.name}
                  className="h-8 w-8"
                />
              </span>
            ) : (
              <span className="text-[28px] font-semibold tracking-tight text-[var(--color-tertiary-label)]">
                {initial_letter}
              </span>
            )}
          </span>
        </div>

        {locked ? (
          <Tip>
            This is the built-in official entry. Switching to it clears every setting U-Pool manages so
            the vendor&apos;s own sign-in takes over - only the label and notes can be edited.
          </Tip>
        ) : !editing && activePreset && activePreset.id !== "custom" ? (
          <Tip>
            Template from CC Switch-style presets. URL and model are filled — paste your API key and
            save. Switch to Custom for a blank form.
          </Tip>
        ) : null}

        <div className="mt-5 grid gap-5 sm:grid-cols-2">
          <Field label="Provider name">
            <TextInput
              value={draft.name}
              autoFocus
              placeholder="e.g. Kimi For Coding"
              onChange={(event) => set("name", event.target.value)}
            />
          </Field>
          <Field label="Note">
            <TextInput
              value={draft.note}
              placeholder="e.g. work account"
              onChange={(event) => set("note", event.target.value)}
            />
          </Field>
        </div>

        <div className="mt-5">
          <Field label="Website">
            <TextInput
              value={draft.website}
              placeholder="https://example.com (optional)"
              onChange={(event) => set("website", event.target.value)}
            />
          </Field>
        </div>

        {locked ? null : (
          <>
            <div className="mt-5">
              <Field
                label="API key"
                hint={
                  editing
                    ? "Leave blank to keep the key that is already stored."
                    : "Stored in ~/.u-pool/config.json and written to the live config on switch."
                }
              >
                <SecretInput
                  value={draft.api_key}
                  placeholder={editing ? "Unchanged" : "sk-..."}
                  onChange={(event) => set("api_key", event.target.value)}
                />
              </Field>
            </div>

            <div className="mt-5 space-y-2">
              <Field label="Request URL">
                <TextInput
                  value={draft.base_url}
                  placeholder="https://your-api-endpoint.com"
                  onChange={(event) => set("base_url", event.target.value)}
                />
              </Field>
              <Tip>{URL_HINT[app]}</Tip>
            </div>
          </>
        )}

        <div className="mt-6 border-t border-zinc-100 pt-4">
          <button
            type="button"
            onClick={() => setAdvanced((v) => !v)}
            className="flex items-center gap-1.5 text-sm font-medium text-zinc-700 hover:text-zinc-900"
          >
            <ChevronIcon className={cx("h-4 w-4 transition", advanced && "rotate-90")} />
            Advanced options
          </button>
          <p className="mt-1 pl-5.5 text-xs text-zinc-400">
            Model override, authentication style and pass-through fields. Defaults are fine for most
            providers.
          </p>

          {advanced ? (
            <div className="animate-fade-in mt-4 space-y-5">
              <div className="grid gap-5 sm:grid-cols-2">
                <Field label="Model" hint="Leave blank to use the provider default.">
                  <TextInput
                    value={draft.model}
                    placeholder={app === "claude" ? "claude-sonnet-4-5" : "gpt-5-codex"}
                    onChange={(event) => set("model", event.target.value)}
                  />
                </Field>

                {app === "claude" ? (
                  <Field
                    label="Authentication header"
                    hint="Relays usually take a bearer token; native Anthropic keys use x-api-key."
                  >
                    <Select
                      value={draft.auth_style}
                      onChange={(event) =>
                        set("auth_style", event.target.value as Draft["auth_style"])
                      }
                    >
                      <option value="auth_token">Authorization: Bearer (ANTHROPIC_AUTH_TOKEN)</option>
                      <option value="api_key">x-api-key (ANTHROPIC_API_KEY)</option>
                    </Select>
                  </Field>
                ) : (
                  <Field label="Wire API" hint="Responses is the modern OpenAI protocol.">
                    <Select
                      value={draft.wire_api}
                      onChange={(event) => set("wire_api", event.target.value as Draft["wire_api"])}
                    >
                      <option value="responses">responses</option>
                      <option value="chat">chat completions</option>
                    </Select>
                  </Field>
                )}
              </div>

              {app === "claude" ? (
                <Field label="Small/fast model" hint="Optional ANTHROPIC_SMALL_FAST_MODEL override.">
                  <TextInput
                    value={draft.small_fast_model}
                    placeholder="claude-haiku-4-5"
                    onChange={(event) => set("small_fast_model", event.target.value)}
                  />
                </Field>
              ) : (
                <Field
                  label="Key environment variable"
                  hint="Only OPENAI_API_KEY can be written to auth.json; any other name has to be exported by you."
                >
                  <TextInput
                    value={draft.env_key}
                    placeholder="OPENAI_API_KEY"
                    onChange={(event) => set("env_key", event.target.value)}
                  />
                </Field>
              )}

              <div>
                <p className="mb-1.5 text-sm font-medium text-zinc-700">
                  {app === "claude" ? "Extra environment variables" : "Extra provider fields"}
                </p>
                <p className="mb-3 text-xs text-zinc-400">
                  {app === "claude"
                    ? "Written into the env block of settings.json and removed again when you switch away."
                    : "Written into the [model_providers] table for this provider."}
                </p>
                <ExtraEditor
                  entries={extra}
                  onChange={setExtra}
                  keyLabel={app === "claude" ? "ENV_NAME" : "field"}
                />
              </div>
            </div>
          ) : null}
        </div>
        </div>
      </div>

      <div className="fixed inset-x-0 bottom-0 px-4 pb-4 pt-2">
        <div className="liquid-glass relative z-[1] mx-auto flex max-w-[720px] items-center justify-between gap-4 rounded-[20px] px-4 py-3">
          <p className={cx("relative z-[1] text-[13px]", error ? "text-red-600" : "text-[var(--color-tertiary-label)]")}>
            {error ?? "Changes take effect on the live config the moment this provider is in use."}
          </p>
          <div className="relative z-[1] flex gap-2">
            <Button onClick={onCancel}>Cancel</Button>
            <Button variant="primary" onClick={submit} disabled={saving}>
              {saving ? "Saving..." : editing ? "Save" : "Add provider"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
