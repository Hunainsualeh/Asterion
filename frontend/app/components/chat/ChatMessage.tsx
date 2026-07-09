import { agentLabel } from "@/app/components/agentTheme";
import Avatar from "@/app/components/ui/Avatar";
import type { Tone } from "@/lib/api";

function timeLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const TONE_ACCENT: Record<Tone, string> = {
  info: "",
  progress: "",
  waiting: "",
  success: "border-success-border",
  error: "border-danger-border",
};

/** One agent narration bubble in the main conversation. */
export default function ChatMessage({
  agent,
  headline,
  detail,
  tone,
  ts,
}: {
  agent: string;
  headline: string;
  detail?: string;
  tone: Tone;
  ts: number;
}) {
  return (
    <div className="animate-in flex items-start gap-3">
      <Avatar agent={agent} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-semibold text-text-secondary">{agentLabel(agent)}</span>
          <span className="text-[11px] text-text-tertiary">{timeLabel(ts)}</span>
        </div>
        <div
          className={`mt-1 max-w-xl rounded-2xl rounded-tl-sm border bg-bubble-assistant-bg px-4 py-2.5 text-sm leading-relaxed text-text-primary shadow-sm ${
            TONE_ACCENT[tone] || "border-bubble-assistant-border"
          }`}
        >
          <p>{headline}</p>
          {detail && <p className="mt-1.5 whitespace-pre-line text-text-secondary">{detail}</p>}
        </div>
      </div>
    </div>
  );
}
