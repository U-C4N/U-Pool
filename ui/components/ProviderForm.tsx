"use client";

import { useMemo, useState } from "react";
import { type ProviderPreset, presetsFor } from "@/lib/presets";
import type { AppId, ProviderDetail, ProviderToggles } from "@/lib/types";
import { PresetIcon } from "./BrandMarks";
import { ArrowLeftIcon, ChevronIcon, PlusIcon, XIcon } from "./icons";
import {
  Button,
  Checkbox,
  Field,
  IconButton,
  SecretInput,
  Select,
  TextInput,
  Tip,
  cx,
} from "./ui";

/** The config key a checkbox owns, so the hint says exactly what gets written. */
function Key({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded-[5px] bg-black/[0.05] px-1 py-px font-mono text-[11px] text-[var(--color-secondary-label)]">
      {children}
    </code>
  );
}

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
    bypass_permissions: false,
    skip_bypass_prompt: false,
    accept_edits: false,
    all_project_mcp: false,
    bypass_approvals: false,
    web_search: false,
    official: false,
  };
}

/** Toggles are the user's choice, not the template's, so they ride along. */
function carriedOver(draft: Draft): ProviderToggles {
  return {
    bypass_permissions: draft.bypass_permissions,
    skip_bypass_prompt: draft.skip_bypass_prompt,
    accept_edits: draft.accept_edits,
    all_project_mcp: draft.all_project_mcp,
    bypass_approvals: draft.bypass_approvals,
    web_search: draft.web_search,
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

/**
 * Claude Desktop drives the same Anthropic settings as Claude Code, so the form
 * asks this rather than naming both ids at every branch - one of them would get
 * missed the next time a field is added.
 */
function isAnthropic(app: AppId): boolean {
  return app === "claude" || app === "claude_desktop";
}

const CLAUDE_URL_HINT =
  "Any Claude-compatible endpoint. Enter the base address without a trailing slash - U-Pool appends the API paths.";

const URL_HINT: Record<AppId, string> = {
  claude: CLAUDE_URL_HINT,
  claude_desktop: CLAUDE_URL_HINT,
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
  mode,
  initial,
  saving,
  onCancel,
  onSave,
}: {
  app: AppId;
  appLabel: string;
  /** Stated by the caller, never guessed from the record - see `editing` below. */
  mode: "add" | "edit";
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

  // Derived from the prop alone. Reading it off `initial?.id` is what put "Add
  // provider" on the button while an existing provider was being edited.
  const editing = mode === "edit";
  const locked = Boolean(initial?.official);
  const anthropic = isAnthropic(app);
  const activePreset = catalog.find((p) => p.id === presetId);
  const initial_letter = draft.name.trim().charAt(0).toUpperCase() || "?";

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  const applyPreset = (preset: ProviderPreset) => {
    setPresetId(preset.id);
    setError(null);
    setExtra(Object.entries(preset.extra ?? {}));
    // Keep what the user typed or ticked when hopping between presets - a template
    // only supplies the endpoint, never the permission switches.
    setDraft((current) => ({
      ...draftFromPreset(app, preset, current.api_key),
      ...carriedOver(current),
    }));
  };

  const extraMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const [key, value] of extra) {
      if (key.trim()) map[key.trim()] = value;
    }
    return map;
  }, [extra]);

  const wideOpen = anthropic ? draft.bypass_permissions : draft.bypass_approvals;

  const toggles = anthropic ? (
    <>
      <Checkbox
        danger
        label="Bypass permission prompts"
        hint={
          <>
            <Key>permissions.defaultMode: &quot;bypassPermissions&quot;</Key> — what{" "}
            <Key>--dangerously-skip-permissions</Key> does, made permanent.
          </>
        }
        checked={draft.bypass_permissions}
        onChange={(value) =>
          // The companion box only means anything in bypass mode, so it does not
          // stay ticked-but-dead when bypass goes away.
          setDraft((current) => ({
            ...current,
            bypass_permissions: value,
            skip_bypass_prompt: value && current.skip_bypass_prompt,
          }))
        }
      />
      <Checkbox
        danger
        label="Skip the bypass warning screen"
        hint={
          <>
            <Key>permissions.skipDangerousModePermissionPrompt</Key> — drops the one-off
            accept-the-risk dialog that bypass mode opens with. Needs bypass above.
          </>
        }
        checked={draft.skip_bypass_prompt}
        disabled={!draft.bypass_permissions}
        onChange={(value) => set("skip_bypass_prompt", value)}
      />
      <Checkbox
        label="Auto-accept file edits"
        hint={
          <>
            <Key>permissions.defaultMode: &quot;acceptEdits&quot;</Key> — edits go through, commands
            still ask. Ignored while bypass is on.
          </>
        }
        checked={draft.accept_edits}
        disabled={draft.bypass_permissions}
        onChange={(value) => set("accept_edits", value)}
      />
      <Checkbox
        label="Trust MCP servers from the project"
        hint={
          <>
            <Key>enableAllProjectMcpServers</Key> — approves every server a repository&apos;s
            .mcp.json declares.
          </>
        }
        checked={draft.all_project_mcp}
        onChange={(value) => set("all_project_mcp", value)}
      />
    </>
  ) : (
    <>
      <Checkbox
        danger
        label="Bypass approvals & sandbox"
        hint={
          <>
            <Key>approval_policy = &quot;never&quot;</Key> plus{" "}
            <Key>sandbox_mode = &quot;danger-full-access&quot;</Key> — the pair{" "}
            <Key>--dangerously-bypass-approvals-and-sandbox</Key> sets.
          </>
        }
        checked={draft.bypass_approvals}
        onChange={(value) => set("bypass_approvals", value)}
      />
      <Checkbox
        label="Live web search"
        hint={
          <>
            <Key>web_search = &quot;live&quot;</Key> at the root of config.toml. The{" "}
            <Key>[tools]</Key> boolean is a no-op in Codex, so it is not used.
          </>
        }
        checked={draft.web_search}
        onChange={(value) => set("web_search", value)}
      />
    </>
  );

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
                {catalog.length} templates
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
            Template ready — URL and model are filled. Paste your API key and save. Switch to
            Custom for a blank form.
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
            Model override, authentication style, permission switches and pass-through fields.
            Defaults are fine for most providers.
          </p>

          {advanced ? (
            <div className="animate-fade-in mt-4 space-y-5">
              <div className="grid gap-5 sm:grid-cols-2">
                <Field label="Model" hint="Leave blank to use the provider default.">
                  <TextInput
                    value={draft.model}
                    placeholder={anthropic ? "claude-sonnet-4-5" : "gpt-5-codex"}
                    onChange={(event) => set("model", event.target.value)}
                  />
                </Field>

                {anthropic ? (
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

              {anthropic ? (
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
                  hint="The variable Codex reads the key from. U-Pool sets it in your Windows environment, so a name other than OPENAI_API_KEY works too."
                >
                  <TextInput
                    value={draft.env_key}
                    placeholder="OPENAI_API_KEY"
                    onChange={(event) => set("env_key", event.target.value)}
                  />
                </Field>
              )}

              {locked ? null : (
                <div>
                  <p className="mb-1.5 text-sm font-medium text-zinc-700">
                    {anthropic ? "Permissions & MCP" : "Approvals & sandbox"}
                  </p>
                  <p className="mb-2 text-xs text-zinc-400">
                    {anthropic
                      ? "Each box owns one key in settings.json while it is ticked, and takes it back out when you untick it. Your allow/deny rules are never touched."
                      : "Codex-wide keys written next to model_provider in config.toml, removed again when you untick the box."}
                  </p>
                  <div className="space-y-0.5">{toggles}</div>
                  {wideOpen ? (
                    <p className="mt-2 rounded-[14px] bg-red-500/[0.07] px-3.5 py-2.5 text-[12px] leading-relaxed text-red-700">
                      Wide open: while this provider is in use, {appLabel} runs commands and edits
                      files without asking. Keep it for endpoints and machines you trust.
                    </p>
                  ) : null}
                </div>
              )}

              <div>
                <p className="mb-1.5 text-sm font-medium text-zinc-700">
                  {anthropic ? "Extra environment variables" : "Extra provider fields"}
                </p>
                <p className="mb-3 text-xs text-zinc-400">
                  {anthropic
                    ? "Written into the env block of settings.json and removed again when you switch away."
                    : "Written into the [model_providers] table for this provider."}
                </p>
                <ExtraEditor
                  entries={extra}
                  onChange={setExtra}
                  keyLabel={anthropic ? "ENV_NAME" : "field"}
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
              {saving ? "Saving..." : editing ? "Save changes" : "Add provider"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
