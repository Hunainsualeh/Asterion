import type { ReactNode } from "react";

export type Tone = "info" | "progress" | "waiting" | "success" | "error" | "neutral";

const TONE_CLASSES: Record<Tone, string> = {
  info: "bg-accent-soft text-accent border-accent-soft-border",
  progress: "bg-accent-soft text-accent border-accent-soft-border",
  waiting: "bg-warning-bg text-warning border-warning-border",
  success: "bg-success-bg text-success border-success-border",
  error: "bg-danger-bg text-danger border-danger-border",
  neutral: "bg-surface-2 text-text-secondary border-border-soft",
};

export default function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]}`}>
      {children}
    </span>
  );
}
