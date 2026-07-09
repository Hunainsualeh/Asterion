"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Tone = "auto" | "concise" | "balanced" | "detailed" | "custom";

export const TONE_LABEL: Record<Tone, string> = {
  auto: "Auto",
  concise: "Concise",
  balanced: "Balanced",
  detailed: "Detailed",
  custom: "Custom",
};

export const TONE_HINT: Record<Tone, string> = {
  auto: "Let each agent choose",
  concise: "Short, direct answers",
  balanced: "A sensible middle ground",
  detailed: "Thorough, with reasoning",
  custom: "Your own instruction",
};

const TONE_DIRECTIVE: Record<Exclude<Tone, "custom">, string> = {
  auto: "",
  balanced: "",
  concise: "Respond concisely — short, direct, minimal preamble or filler.",
  detailed:
    "Respond thoroughly — explain your reasoning and cover the important detail, trade-offs, and edge cases.",
};

/** The style directive sent to the backend for the current tone. "" = default. */
export function toneDirective(tone: Tone, custom: string): string {
  return tone === "custom" ? custom.trim() : TONE_DIRECTIVE[tone];
}

const TONE_KEY = "asterion:tone";
const CUSTOM_KEY = "asterion:tone-custom";

interface SettingsValue {
  tone: Tone;
  setTone: (t: Tone) => void;
  custom: string;
  setCustom: (s: string) => void;
  /** Resolved directive for the current tone (empty when default). */
  directive: string;
}

const SettingsContext = createContext<SettingsValue | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [tone, setToneState] = useState<Tone>("auto");
  const [custom, setCustomState] = useState("");

  useEffect(() => {
    const t = localStorage.getItem(TONE_KEY) as Tone | null;
    if (t && t in TONE_LABEL) setToneState(t);
    setCustomState(localStorage.getItem(CUSTOM_KEY) ?? "");
  }, []);

  function setTone(t: Tone) {
    setToneState(t);
    localStorage.setItem(TONE_KEY, t);
  }
  function setCustom(s: string) {
    setCustomState(s);
    localStorage.setItem(CUSTOM_KEY, s);
  }

  return (
    <SettingsContext.Provider value={{ tone, setTone, custom, setCustom, directive: toneDirective(tone, custom) }}>
      {children}
    </SettingsContext.Provider>
  );
}

export function useSettings() {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error("useSettings must be used within SettingsProvider");
  return ctx;
}
