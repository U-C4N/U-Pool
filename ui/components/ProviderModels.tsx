"use client";

import { useEffect, useId, useRef, useState } from "react";
import { backend } from "@/lib/bridge";
import type { ModelCatalog, ProviderDetail } from "@/lib/types";
import { PlusIcon, XIcon } from "./icons";
import { Button, IconButton, TextInput, cx } from "./ui";

export function ProviderModels({
  provider, onSelect, onModelsChange, onSelectFast,
}: {
  provider: Partial<ProviderDetail>;
  onSelect: (id: string) => void;
  onModelsChange: (models: string[]) => void;
  onSelectFast?: (id: string) => void;
}) {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [manual, setManual] = useState("");
  const requestId = useRef(0);
  const headingId = useId();
  const { app, base_url, api_key, auth_style, transport, npm, official } = provider;
  const selected = provider.models ?? [];
  const selectedModels = Array.from(new Set([provider.model?.trim(), ...selected].filter((id): id is string => Boolean(id))));

  const addModels = (ids: string[]) => {
    const added = ids.map((id) => id.trim()).filter(Boolean);
    if (!added.length) return;
    onModelsChange(Array.from(new Set([...selectedModels, ...added])));
    if (!provider.model?.trim()) onSelect(added[0]);
  };
  const addManual = () => {
    addModels(manual.split(/[\n,]+/));
    setManual("");
  };
  const removeModel = (id: string) => {
    const remaining = selectedModels.filter((model) => model !== id);
    onModelsChange(selected.filter((model) => model !== id));
    if (provider.model === id) onSelect(remaining[0] ?? "");
  };

  useEffect(() => {
    // A late response must not populate a different endpoint/key's model list.
    requestId.current += 1;
    setCatalog(null);
    setError("");
    setBusy(false);
    setQuery("");
    return () => { requestId.current += 1; };
  }, [app, base_url, api_key, auth_style, transport, npm, official]);

  const load = async () => {
    const id = ++requestId.current;
    setBusy(true);
    setError("");
    setCatalog(null);
    try {
      const result = await backend.listProviderModels({
        app, base_url, api_key, auth_style, transport, npm, official,
      });
      if (id === requestId.current) setCatalog(result);
    } catch (err) {
      if (id === requestId.current) setError(err instanceof Error ? err.message : "Could not load models.");
    } finally {
      if (id === requestId.current) setBusy(false);
    }
  };
  const models = catalog?.models.filter((model) =>
    `${model.id} ${model.name}`.toLowerCase().includes(query.trim().toLowerCase()),
  ) ?? [];

  return (
    <section aria-labelledby={headingId} className="rounded-[18px] border border-black/[0.07] bg-white/40 p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 id={headingId} className="text-sm font-semibold text-zinc-800">Provider models</h3>
          <p className="mt-1 text-xs text-zinc-500">Add multiple models from the live catalogue or enter IDs manually, then choose a default.</p>
        </div>
        <Button className="shrink-0 whitespace-nowrap" onClick={load} disabled={busy || !base_url?.trim() || official}>
          {busy ? "Loading…" : catalog ? "Refresh models" : "Load models"}
        </Button>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2 sm:flex-nowrap">
        <TextInput
          aria-label="Model IDs to add"
          className="min-w-0 flex-1 basis-full sm:basis-auto"
          value={manual}
          placeholder="Model ID, or several IDs separated by commas"
          onChange={(event) => setManual(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addManual();
            }
          }}
        />
        <Button className="shrink-0 whitespace-nowrap" onClick={addManual} disabled={!manual.trim()}>
          <PlusIcon className="h-4 w-4" />
          Add models
        </Button>
      </div>
      {selectedModels.length ? (
        <div className="mt-3">
          <p className="mb-2 text-xs font-medium text-zinc-600">Saved models · {selectedModels.length}</p>
          <ul aria-label="Saved provider models" className="max-h-52 space-y-1 overflow-y-auto">
            {selectedModels.map((id) => (
              <li key={id} className="flex items-center gap-2 rounded-xl bg-white/55 px-3 py-2">
                <span className="min-w-0 flex-1 break-all text-xs font-medium text-zinc-800">{id}</span>
                <button
                  type="button"
                  aria-label={`Use ${id} as default model`}
                  aria-pressed={provider.model === id}
                  onClick={() => {
                    onModelsChange(selectedModels);
                    onSelect(id);
                  }}
                  className={cx("shrink-0 rounded-lg px-2 py-1 text-[11px] hover:bg-white/80", provider.model === id ? "bg-brand-600/10 text-brand-700" : "text-zinc-500")}
                >
                  {provider.model === id ? "Default" : "Set default"}
                </button>
                <IconButton label={`Remove ${id}`} onClick={() => removeModel(id)}>
                  <XIcon className="h-3.5 w-3.5" />
                </IconButton>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-zinc-500">
            {app === "claude" ? "Added models appear in the /model picker with Claude Code 2.1.242 or later." :
              app === "claude_desktop" ? "Models are saved with this profile. Claude Desktop remains preview-only." :
              app === "codex" ? "Models are saved with this profile. Set a default here to switch the active model." :
              "Added models are included in this provider's model list."}
          </p>
        </div>
      ) : null}
      {error ? <p role="alert" className="mt-3 text-xs text-red-600">{error}</p> : null}
      {catalog ? (
        <>
          <p role="status" className="mt-3 text-xs text-zinc-500">
            {catalog.demo ? "Browser preview · sample data · " : "Live catalogue · "}
            {catalog.models.length} models
          </p>
          {catalog.models.length ? (
            <>
              <TextInput aria-label="Search provider models" className="mt-3" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search model IDs or names" />
              <ul aria-label="Available provider models" className="mt-2 max-h-64 space-y-1 overflow-y-auto">
                {models.map((model) => (
                  <li key={model.id} className="flex items-center gap-2">
                    <button type="button" onClick={() => {
                      if (selectedModels.includes(model.id)) removeModel(model.id);
                      else addModels([model.id]);
                    }} aria-label={`${selectedModels.includes(model.id) ? "Remove" : "Add"} ${model.id}`} aria-pressed={selectedModels.includes(model.id)}
                      className={cx("min-w-0 flex-1 rounded-xl px-3 py-2 text-left transition hover:bg-white/80", selectedModels.includes(model.id) && "bg-brand-600/10 ring-1 ring-brand-600/25")}>
                      <span className="block break-all text-xs font-medium text-zinc-800">{model.id}</span>
                      {model.name !== model.id ? <span className="block text-xs text-zinc-500">{model.name}</span> : null}
                      {model.context_length ? <span className="text-[11px] text-zinc-500">{model.context_length.toLocaleString()} context tokens</span> : null}
                    </button>
                    <span className="shrink-0 text-[11px] text-zinc-500">{selectedModels.includes(model.id) ? "Added" : "Add"}</span>
                    {onSelectFast ? (
                      <button type="button" onClick={() => onSelectFast(model.id)} aria-label={`Use ${model.id} as small/fast model`}
                        aria-pressed={provider.small_fast_model === model.id}
                        className={cx("shrink-0 rounded-lg px-2 py-2 text-[11px] hover:bg-white/80", provider.small_fast_model === model.id ? "bg-brand-600/10 text-brand-700" : "text-zinc-500")}>
                        Small/fast
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
              {!models.length ? <p className="mt-2 text-xs text-zinc-500">No models match your search.</p> : null}
            </>
          ) : <p className="mt-2 text-xs text-zinc-500">The provider returned an empty catalogue. You can still enter a model ID manually.</p>}
        </>
      ) : null}
    </section>
  );
}
