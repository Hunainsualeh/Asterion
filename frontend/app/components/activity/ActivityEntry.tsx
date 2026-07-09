import { agentLabel } from "@/app/components/agentTheme";
import type { PipelineEvent } from "@/lib/api";

const KIND_DOT: Record<string, string> = {
  gate: "bg-warning",
  error: "bg-danger",
  done: "bg-success",
};

function timeLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function ActivityEntry({ event }: { event: PipelineEvent }) {
  return (
    <li className="relative">
      <span className={`absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full border-2 border-bg ${KIND_DOT[event.kind] ?? "bg-accent"}`} />
      <div className="flex items-center gap-2">
        <span className="rounded-full border border-border-soft bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium text-text-secondary">
          {agentLabel(event.agent)}
        </span>
        <span className="font-mono text-[10px] text-text-tertiary">{event.kind}</span>
        <span className="text-[10px] text-text-tertiary">{timeLabel(event.ts)}</span>
      </div>
      <p className="mt-1 text-sm leading-snug text-text-secondary">{event.message}</p>
    </li>
  );
}
