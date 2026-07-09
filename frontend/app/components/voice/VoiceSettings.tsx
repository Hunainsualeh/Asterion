"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Plus, RotateCcw, Upload, X } from "lucide-react";
import { useVoiceConfig } from "@/hooks/useVoiceConfig";
import { RECOGNITION_LANGS, VOICE_ACTION_LABEL, type VoiceCommand } from "@/lib/voice";

export default function VoiceSettings() {
  const cfg = useVoiceConfig();
  const { settings, commands, wakeWords } = cfg;
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined" || !window.speechSynthesis) return;
    const load = () => setVoices(window.speechSynthesis.getVoices());
    load();
    window.speechSynthesis.onvoiceschanged = load;
    return () => {
      if (window.speechSynthesis) window.speechSynthesis.onvoiceschanged = null;
    };
  }, []);

  return (
    <div className="flex flex-col gap-6 pb-2">
      {/* ---- voice mode ---- */}
      <Section title="Voice mode">
        <Toggle
          label="Wake-word listening"
          hint="Listen in the background for a wake word (uses the mic continuously)."
          checked={settings.wakeWordEnabled}
          onChange={(v) => cfg.setSetting("wakeWordEnabled", v)}
        />
        <Toggle
          label="Continuous conversation"
          hint="Keep listening after each reply instead of stopping."
          checked={settings.continuous}
          onChange={(v) => cfg.setSetting("continuous", v)}
        />
        <Toggle
          label="Push-to-talk"
          hint="Hold Space (or the mic) to talk; release to send."
          checked={settings.pushToTalk}
          onChange={(v) => cfg.setSetting("pushToTalk", v)}
        />
        <Toggle
          label="Spoken responses"
          hint="Read the assistant's replies aloud."
          checked={settings.ttsEnabled}
          onChange={(v) => cfg.setSetting("ttsEnabled", v)}
        />
        <Toggle
          label="Sound cues"
          hint="Play a chime when listening starts and stops."
          checked={settings.soundCues}
          onChange={(v) => cfg.setSetting("soundCues", v)}
        />
        <Toggle
          label="Noise cancellation"
          hint="Suppress background noise on the microphone."
          checked={settings.noiseCancellation}
          onChange={(v) => cfg.setSetting("noiseCancellation", v)}
        />

        <div className="grid grid-cols-2 gap-3 pt-1">
          <SelectField label="Language" value={settings.recognitionLang} onChange={(v) => cfg.setSetting("recognitionLang", v)}>
            {RECOGNITION_LANGS.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </SelectField>
          <SelectField label="Voice" value={settings.ttsVoiceURI} onChange={(v) => cfg.setSetting("ttsVoiceURI", v)}>
            <option value="">Default</option>
            {voices.map((v) => (
              <option key={v.voiceURI} value={v.voiceURI}>
                {v.name} ({v.lang})
              </option>
            ))}
          </SelectField>
        </div>
        <Slider label="Response speed" min={0.5} max={2} step={0.1} value={settings.ttsRate} onChange={(v) => cfg.setSetting("ttsRate", v)} suffix="×" />
        <Slider label="Mic sensitivity" min={0} max={1} step={0.05} value={settings.sensitivity} onChange={(v) => cfg.setSetting("sensitivity", v)} />
        <Slider label="Wake-word sensitivity" min={0} max={1} step={0.05} value={settings.wakeSensitivity} onChange={(v) => cfg.setSetting("wakeSensitivity", v)} />
        <Slider
          label="Auto-listening timeout"
          min={2000}
          max={20000}
          step={1000}
          value={settings.autoTimeoutMs}
          onChange={(v) => cfg.setSetting("autoTimeoutMs", v)}
          format={(v) => `${Math.round(v / 1000)}s`}
        />
      </Section>

      {/* ---- wake words ---- */}
      <Section title="Wake words">
        <ChipEditor
          items={wakeWords}
          placeholder="e.g. hey friday"
          onAdd={cfg.addWakeWord}
          onRemove={cfg.removeWakeWord}
        />
      </Section>

      {/* ---- commands ---- */}
      <Section title="Commands">
        <p className="-mt-1 mb-1 text-xs text-text-tertiary">
          Map any spoken phrase to an action. Multiple phrases can trigger the same action.
        </p>
        <ul className="flex flex-col gap-2">
          {commands.map((cmd) => (
            <CommandRow key={cmd.id} cmd={cmd} />
          ))}
        </ul>
      </Section>

      {/* ---- import / export ---- */}
      <Section title="Configuration">
        <div className="flex flex-wrap gap-2">
          <ActionBtn icon={<Download size={14} />} label="Export" onClick={cfg.exportConfig} />
          <ActionBtn icon={<Upload size={14} />} label="Import" onClick={() => fileRef.current?.click()} />
          <ActionBtn icon={<RotateCcw size={14} />} label="Reset to defaults" onClick={cfg.reset} />
          <input
            ref={fileRef}
            type="file"
            accept="application/json"
            className="hidden"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              const ok = cfg.importConfig(await file.text());
              setImportError(ok ? null : "That file wasn't a valid voice configuration.");
              e.target.value = "";
            }}
          />
        </div>
        {importError && <p className="mt-2 text-xs text-danger">{importError}</p>}
      </Section>
    </div>
  );
}

function CommandRow({ cmd }: { cmd: VoiceCommand }) {
  const cfg = useVoiceConfig();
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  return (
    <li className={`rounded-xl border p-3 ${cmd.enabled ? "border-border" : "border-border-soft opacity-60"}`}>
      <div className="mb-2 flex items-center gap-2">
        <span className="flex-1 text-sm font-medium text-text-primary">{VOICE_ACTION_LABEL[cmd.action]}</span>
        <MiniToggle checked={cmd.enabled} onChange={(v) => cfg.toggleCommand(cmd.id, v)} />
      </div>
      <div className="flex flex-wrap gap-1.5">
        {cmd.phrases.map((p) => (
          <span
            key={p}
            className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-1 text-xs text-text-secondary"
          >
            {p}
            <button
              onClick={() => cfg.removePhrase(cmd.id, p)}
              aria-label={`Remove phrase ${p}`}
              className="text-text-tertiary hover:text-danger"
            >
              <X size={11} />
            </button>
          </span>
        ))}
        {adding ? (
          <input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => {
              if (draft.trim()) cfg.addPhrase(cmd.id, draft.trim());
              setDraft("");
              setAdding(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                if (draft.trim()) cfg.addPhrase(cmd.id, draft.trim());
                setDraft("");
                setAdding(false);
              }
              if (e.key === "Escape") {
                setDraft("");
                setAdding(false);
              }
            }}
            placeholder="new phrase"
            className="w-28 rounded-full border border-accent bg-surface px-2 py-1 text-xs text-text-primary focus:outline-none"
          />
        ) : (
          <button
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-1 text-xs text-text-tertiary hover:border-accent hover:text-accent"
          >
            <Plus size={11} /> phrase
          </button>
        )}
      </div>
    </li>
  );
}

// --------------------------------------------------------------------------- primitives
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <p className="mb-2 text-xs font-medium uppercase tracking-wider text-text-tertiary">{title}</p>
      <div className="flex flex-col gap-2">{children}</div>
    </section>
  );
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className="flex items-center gap-3 rounded-xl border border-border px-3 py-2 text-left transition-colors hover:bg-surface-2"
    >
      <span className="min-w-0 flex-1">
        <span className="block text-sm text-text-primary">{label}</span>
        {hint && <span className="block text-[11px] text-text-tertiary">{hint}</span>}
      </span>
      <MiniToggle checked={checked} onChange={onChange} />
    </button>
  );
}

function MiniToggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <span
      role="switch"
      aria-checked={checked}
      onClick={(e) => {
        e.stopPropagation();
        onChange(!checked);
      }}
      className={`flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full p-0.5 transition-colors ${
        checked ? "bg-accent" : "bg-border"
      }`}
    >
      <span className={`h-4 w-4 rounded-full bg-surface shadow transition-transform ${checked ? "translate-x-4" : ""}`} />
    </span>
  );
}

function Slider({
  label,
  min,
  max,
  step,
  value,
  onChange,
  suffix = "",
  format,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (v: number) => void;
  suffix?: string;
  format?: (v: number) => string;
}) {
  return (
    <label className="flex flex-col gap-1 px-1">
      <span className="flex items-center justify-between text-xs text-text-secondary">
        <span>{label}</span>
        <span className="font-mono text-text-tertiary">{format ? format(value) : `${value}${suffix}`}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="accent-accent"
      />
    </label>
  );
}

function SelectField({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-medium uppercase tracking-wide text-text-tertiary">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-border bg-surface px-2.5 py-2 text-sm text-text-primary focus:border-accent focus:outline-none"
      >
        {children}
      </select>
    </label>
  );
}

function ChipEditor({
  items,
  placeholder,
  onAdd,
  onRemove,
}: {
  items: string[];
  placeholder: string;
  onAdd: (v: string) => void;
  onRemove: (v: string) => void;
}) {
  const [draft, setDraft] = useState("");
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((w) => (
        <span key={w} className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2.5 py-1 text-xs text-accent">
          {w}
          <button onClick={() => onRemove(w)} aria-label={`Remove ${w}`} className="hover:text-danger">
            <X size={11} />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && draft.trim()) {
            onAdd(draft.trim());
            setDraft("");
          }
        }}
        placeholder={placeholder}
        className="w-36 rounded-full border border-border bg-surface px-2.5 py-1 text-xs text-text-primary placeholder:text-text-tertiary focus:border-accent focus:outline-none"
      />
    </div>
  );
}

function ActionBtn({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-text-primary transition-colors hover:bg-surface-2"
    >
      {icon}
      {label}
    </button>
  );
}
