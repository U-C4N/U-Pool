"use client";

import { useEffect, useId, useRef, useState } from "react";
import { backend } from "@/lib/bridge";
import type { ModelCatalog, ProviderDetail } from "@/lib/types";
import { Button, TextInput, cx } from "./ui";

export function ProviderModels({
  provider, onSelect, onSelectFast,
}: {
  provider: Partial<ProviderDetail>;
  onSelect: (id: string) => void;
  onSelectFast?: (id: string) => void;
}) {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef(0);
  const headingId = useId();
  const { app, base_url, api_key, auth_style, transport, npm, official } = provider;

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
          <p className="mt-1 text-xs text-zinc-500">Load every model available to this API key, then choose one below.</p>
        </div>
        <Button onClick={load} disabled={busy || !base_url?.trim() || official}>
          {busy ? "Loading…" : catalog ? "Refresh models" : "Load models"}
        </Button>
      </div>
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
                    <button type="button" onClick={() => onSelect(model.id)} aria-pressed={provider.model === model.id}
                      className={cx("min-w-0 flex-1 rounded-xl px-3 py-2 text-left transition hover:bg-white/80", provider.model === model.id && "bg-brand-600/10 ring-1 ring-brand-600/25")}>
                      <span className="block break-all text-xs font-medium text-zinc-800">{model.id}</span>
                      {model.name !== model.id ? <span className="block text-xs text-zinc-500">{model.name}</span> : null}
                      {model.context_length ? <span className="text-[11px] text-zinc-500">{model.context_length.toLocaleString()} context tokens</span> : null}
                    </button>
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
