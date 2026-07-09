"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  DEFAULT_VOICE_SETTINGS,
  DEFAULT_WAKE_WORDS,
  defaultCommands,
  WAKE_WORD_ENABLED,
  type VoiceActionId,
  type VoiceCommand,
  type VoiceSettings,
} from "@/lib/voice";

const KEY = "asterion:voice-config";

// Bump when a default change must reach users who already have a saved config
// (whose saved values would otherwise mask the new default forever). v2 makes
// wake-word listening opt-out instead of opt-in.
const CONFIG_VERSION = 2;

/** Force `wakeWordEnabled` to obey the build flag.
 *
 * Every existing user has `wakeWordEnabled: true` sitting in localStorage (v2
 * migrated them onto it deliberately). Without this clamp, disabling the
 * feature in code would do nothing for exactly the people who already have it
 * — their saved config would switch the microphone back on. Applied on load
 * AND on every write, so an imported config file can't smuggle it back in. */
function clamp(settings: VoiceSettings): VoiceSettings {
  if (WAKE_WORD_ENABLED || !settings.wakeWordEnabled) return settings;
  return { ...settings, wakeWordEnabled: false };
}

interface VoiceConfig {
  settings: VoiceSettings;
  commands: VoiceCommand[];
  wakeWords: string[];
}

interface VoiceConfigValue extends VoiceConfig {
  ready: boolean;
  setSetting: <K extends keyof VoiceSettings>(key: K, value: VoiceSettings[K]) => void;
  setCommandPhrases: (id: string, phrases: string[]) => void;
  toggleCommand: (id: string, enabled: boolean) => void;
  addPhrase: (id: string, phrase: string) => void;
  removePhrase: (id: string, phrase: string) => void;
  addWakeWord: (word: string) => void;
  removeWakeWord: (word: string) => void;
  reset: () => void;
  exportConfig: () => void;
  importConfig: (json: string) => boolean;
}

const Ctx = createContext<VoiceConfigValue | null>(null);

function load(): VoiceConfig {
  const base: VoiceConfig = {
    settings: DEFAULT_VOICE_SETTINGS,
    commands: defaultCommands(),
    wakeWords: [...DEFAULT_WAKE_WORDS],
  };
  if (typeof window === "undefined") return base;
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return base;
    const parsed = JSON.parse(raw) as Partial<VoiceConfig> & { v?: number };
    const settings = { ...DEFAULT_VOICE_SETTINGS, ...(parsed.settings ?? {}) };
    // One-time migration: configs saved before v2 have wakeWordEnabled:false
    // pinned, which would mask the new opt-out default. Force it on once; the
    // next persist writes v:2 so the user can still turn it back off and have
    // that stick. (Moot while WAKE_WORD_ENABLED is false — clamp() wins.)
    if ((parsed.v ?? 1) < 2) settings.wakeWordEnabled = true;
    return {
      settings: clamp(settings),
      // Merge any newly-shipped default actions the saved config predates.
      commands: mergeCommands(parsed.commands ?? []),
      wakeWords: parsed.wakeWords ?? [...DEFAULT_WAKE_WORDS],
    };
  } catch {
    return base;
  }
}

function mergeCommands(saved: VoiceCommand[]): VoiceCommand[] {
  const byAction = new Map(saved.map((c) => [c.action, c]));
  return defaultCommands().map((def) => byAction.get(def.action) ?? def);
}

export function VoiceConfigProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<VoiceConfig>(() => ({
    settings: DEFAULT_VOICE_SETTINGS,
    commands: defaultCommands(),
    wakeWords: [...DEFAULT_WAKE_WORDS],
  }));
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setConfig(load());
    setReady(true);
  }, []);

  const persist = useCallback((raw: VoiceConfig) => {
    const next = { ...raw, settings: clamp(raw.settings) };
    setConfig(next);
    try {
      localStorage.setItem(KEY, JSON.stringify({ ...next, v: CONFIG_VERSION }));
    } catch {
      /* quota / private mode — keep in-memory */
    }
  }, []);

  const setSetting = useCallback<VoiceConfigValue["setSetting"]>(
    (key, value) => persist({ ...config, settings: { ...config.settings, [key]: value } }),
    [config, persist],
  );

  const updateCommands = useCallback(
    (fn: (cmds: VoiceCommand[]) => VoiceCommand[]) => persist({ ...config, commands: fn(config.commands) }),
    [config, persist],
  );

  const setCommandPhrases = useCallback(
    (id: string, phrases: string[]) =>
      updateCommands((c) => c.map((x) => (x.id === id ? { ...x, phrases: dedupe(phrases) } : x))),
    [updateCommands],
  );
  const toggleCommand = useCallback(
    (id: string, enabled: boolean) => updateCommands((c) => c.map((x) => (x.id === id ? { ...x, enabled } : x))),
    [updateCommands],
  );
  const addPhrase = useCallback(
    (id: string, phrase: string) =>
      updateCommands((c) => c.map((x) => (x.id === id ? { ...x, phrases: dedupe([...x.phrases, phrase]) } : x))),
    [updateCommands],
  );
  const removePhrase = useCallback(
    (id: string, phrase: string) =>
      updateCommands((c) => c.map((x) => (x.id === id ? { ...x, phrases: x.phrases.filter((p) => p !== phrase) } : x))),
    [updateCommands],
  );

  const addWakeWord = useCallback(
    (word: string) => {
      const w = word.trim();
      if (!w) return;
      persist({ ...config, wakeWords: dedupe([...config.wakeWords, w]) });
    },
    [config, persist],
  );
  const removeWakeWord = useCallback(
    (word: string) => persist({ ...config, wakeWords: config.wakeWords.filter((w) => w !== word) }),
    [config, persist],
  );

  const reset = useCallback(
    () => persist({ settings: DEFAULT_VOICE_SETTINGS, commands: defaultCommands(), wakeWords: [...DEFAULT_WAKE_WORDS] }),
    [persist],
  );

  const exportConfig = useCallback(() => {
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "asterion-voice-commands.json";
    a.click();
    URL.revokeObjectURL(url);
  }, [config]);

  const importConfig = useCallback(
    (json: string): boolean => {
      try {
        const parsed = JSON.parse(json) as Partial<VoiceConfig>;
        persist({
          settings: { ...DEFAULT_VOICE_SETTINGS, ...(parsed.settings ?? {}) },
          commands: parsed.commands?.length ? mergeCommands(parsed.commands) : defaultCommands(),
          wakeWords: parsed.wakeWords?.length ? parsed.wakeWords : [...DEFAULT_WAKE_WORDS],
        });
        return true;
      } catch {
        return false;
      }
    },
    [persist],
  );

  const value = useMemo<VoiceConfigValue>(
    () => ({
      ...config,
      ready,
      setSetting,
      setCommandPhrases,
      toggleCommand,
      addPhrase,
      removePhrase,
      addWakeWord,
      removeWakeWord,
      reset,
      exportConfig,
      importConfig,
    }),
    [config, ready, setSetting, setCommandPhrases, toggleCommand, addPhrase, removePhrase, addWakeWord,
     removeWakeWord, reset, exportConfig, importConfig],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function dedupe(arr: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const s of arr.map((x) => x.trim()).filter(Boolean)) {
    const k = s.toLowerCase();
    if (!seen.has(k)) {
      seen.add(k);
      out.push(s);
    }
  }
  return out;
}

export function useVoiceConfig() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useVoiceConfig must be used within VoiceConfigProvider");
  return ctx;
}

export type { VoiceActionId };
