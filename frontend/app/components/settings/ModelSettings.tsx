"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Cpu, Loader2, Sparkles, Zap } from "lucide-react";
import { listModels, setModelSelection, type LLMModel, type LLMProvider, type ModelCatalog } from "@/lib/api";

/** The "no override" pseudo-option. `null` over the wire; a sentinel string
 * here because a radio group needs a comparable value for every row. */
const AUTO = "__auto__";

const TIER_ICON: Record<string, React.ReactNode> = {
  reasoning: <Sparkles size={13} />,
  "long-context": <Cpu size={13} />,
  fast: <Zap size={13} />,
};

export default function ModelSettings() {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Which row is mid-flight, so only that row shows a spinner.
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listModels()
      .then((c) => !cancelled && setCatalog(c))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  const select = useCallback(
    async (id: string) => {
      if (!catalog || saving) return;
      const model = id === AUTO ? null : id;
      const previous = catalog.selected;
      setSaving(id);
      setError(null);
      // Optimistic: the radio moves immediately, and rolls back if the PUT is
      // rejected (e.g. picking DeepSeek with no key configured).
      setCatalog({ ...catalog, selected: model });
      try {
        const { selected } = await setModelSelection(model);
        setCatalog((c) => (c ? { ...c, selected } : c));
      } catch (e) {
        setCatalog((c) => (c ? { ...c, selected: previous } : c));
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setSaving(null);
      }
    },
    [catalog, saving],
  );

  if (error && !catalog) {
    return <p className="py-6 text-center text-sm text-danger">Couldn&apos;t load models: {error}</p>;
  }
  if (!catalog) {
    return (
      <div className="flex items-center justify-center gap-2 py-8 text-sm text-text-tertiary">
        <Loader2 size={14} className="animate-spin" /> Loading models…
      </div>
    );
  }

  const selected = catalog.selected ?? AUTO;
  const byProvider = (id: string) => catalog.models.filter((m) => m.provider === id);

  return (
    <div className="flex flex-col gap-6 pb-2">
      {error && (
        <p className="rounded-lg border border-danger-border bg-danger-bg px-3 py-2 text-xs text-danger">{error}</p>
      )}

      <section>
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-text-tertiary">Model</p>
        <Row
          id={AUTO}
          label="Automatic (recommended)"
          description="Each agent uses the model tuned for its job — deep reasoning for architecture, fast models for high-volume steps."
          selected={selected === AUTO}
          saving={saving === AUTO}
          available
          onSelect={select}
        />
      </section>

      {catalog.providers.map((provider) => (
        <ProviderSection
          key={provider.id}
          provider={provider}
          models={byProvider(provider.id)}
          selected={selected}
          saving={saving}
          onSelect={select}
        />
      ))}

      <p className="text-[11px] leading-relaxed text-text-tertiary">
        Picking a model puts it ahead of every agent&apos;s configured model, and keeps the rest of that agent&apos;s
        chain as fallbacks. If the chosen model is rate-limited, out of credit, or can&apos;t read a long enough
        context, the run silently continues on the next model down instead of failing. Short internal calls
        (intent detection, chat titles, image reading, web search) always stay on their own fast models.
      </p>
    </div>
  );
}

function ProviderSection({
  provider,
  models,
  selected,
  saving,
  onSelect,
}: {
  provider: LLMProvider;
  models: LLMModel[];
  selected: string;
  saving: string | null;
  onSelect: (id: string) => void;
}) {
  if (!models.length) return null;
  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <p className="text-xs font-medium uppercase tracking-wider text-text-tertiary">{provider.label}</p>
        {!provider.configured && (
          <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[10px] text-text-tertiary">no key</span>
        )}
        {provider.configured && provider.keys > 1 && (
          <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[10px] text-text-tertiary">
            {provider.keys} keys
          </span>
        )}
      </div>

      {/* The load-bearing message: a DeepSeek key with no balance authenticates
          and lists its models, then 402s every completion. Without this the
          user would pick a model, see Groq answer anyway, and have no idea why. */}
      {provider.note && (
        <p className="mb-2 flex items-start gap-1.5 rounded-lg border border-warning-border bg-warning-bg px-2.5 py-2 text-[11px] leading-relaxed text-warning">
          <AlertTriangle size={13} className="mt-px shrink-0" />
          <span>{provider.note}</span>
        </p>
      )}

      <div className="flex flex-col gap-2">
        {models.map((m) => (
          <Row
            key={m.id}
            id={m.id}
            label={m.label}
            description={m.description}
            tier={m.tier}
            reasoning={m.reasoning}
            available={m.available}
            selected={selected === m.id}
            saving={saving === m.id}
            onSelect={onSelect}
          />
        ))}
      </div>
    </section>
  );
}

function Row({
  id,
  label,
  description,
  tier,
  reasoning,
  available,
  selected,
  saving,
  onSelect,
}: {
  id: string;
  label: string;
  description: string;
  tier?: string;
  reasoning?: boolean;
  available: boolean;
  selected: boolean;
  saving: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <button
      role="radio"
      aria-checked={selected}
      disabled={!available || saving}
      onClick={() => onSelect(id)}
      className={`flex w-full items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors ${
        selected ? "border-accent bg-accent-soft" : "border-border hover:bg-surface-2"
      } ${!available ? "cursor-not-allowed opacity-50" : ""}`}
    >
      <span
        className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${
          selected ? "border-accent bg-accent text-accent-foreground" : "border-border"
        }`}
      >
        {saving ? <Loader2 size={10} className="animate-spin" /> : selected ? <Check size={10} /> : null}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className={`text-sm font-medium ${selected ? "text-accent" : "text-text-primary"}`}>{label}</span>
          {tier && TIER_ICON[tier] && <span className="text-text-tertiary">{TIER_ICON[tier]}</span>}
          {reasoning && (
            <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] text-text-tertiary">reasoning</span>
          )}
        </span>
        <span className="mt-0.5 block text-[11px] leading-relaxed text-text-tertiary">{description}</span>
      </span>
    </button>
  );
}
