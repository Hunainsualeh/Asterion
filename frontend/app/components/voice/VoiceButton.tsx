"use client";

import { Mic } from "lucide-react";
import { useVoiceControl } from "./VoiceProvider";

/** Mic toggle for voice mode. Pulses while a session is active. Hidden when the
 * browser can't do speech recognition at all. */
export default function VoiceButton({ className = "", size = 18 }: { className?: string; size?: number }) {
  const { toggle, active, supported, status } = useVoiceControl();
  if (!supported) return null;

  const listening = status === "listening";
  return (
    <button
      onClick={toggle}
      aria-label={active ? "Stop voice mode" : "Start voice mode"}
      title={active ? "Stop voice mode" : "Talk to Friday"}
      className={`relative flex items-center justify-center rounded-full transition-colors ${
        active
          ? "bg-accent text-accent-foreground"
          : "text-text-secondary hover:bg-surface-2 hover:text-text-primary"
      } ${className}`}
    >
      {listening && <span className="absolute inset-0 animate-ping rounded-full bg-accent/40" />}
      <Mic size={size} className="relative" />
    </button>
  );
}
