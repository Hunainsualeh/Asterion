import { CheckCircle2, HelpCircle, ThumbsUp } from "lucide-react";
import { agentLabel } from "@/app/components/agentTheme";
import Avatar from "@/app/components/ui/Avatar";
import Badge from "@/app/components/ui/Badge";
import type { GateItem } from "@/lib/timeline";

const GATE_ICON: Record<string, typeof HelpCircle> = {
  clarify: HelpCircle,
  manual_test: CheckCircle2,
  approval: ThumbsUp,
};

function timeLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** A gate event rendered as a chat card — a question, an approval ask, or a
 * manual-test request. `live` ones are still waiting on the human; resolved
 * ones stay in the thread as a record of what was asked. */
export default function GateCard({ item, checklist }: { item: GateItem; checklist?: string[] }) {
  const Icon = GATE_ICON[item.gateKind] ?? HelpCircle;

  return (
    <div className="animate-in flex items-start gap-3">
      <Avatar agent={item.agent} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-semibold text-text-secondary">{agentLabel(item.agent)}</span>
          <span className="text-[11px] text-text-tertiary">{timeLabel(item.ts)}</span>
        </div>
        <div className="mt-1 max-w-xl rounded-2xl rounded-tl-sm border border-warning-border/60 bg-bubble-assistant-bg px-4 py-3 text-sm leading-relaxed text-text-primary shadow-sm">
          <div className="flex items-start gap-2">
            <Icon size={16} className="mt-0.5 shrink-0 text-warning" />
            <p className="font-medium">{item.headline}</p>
          </div>
          {item.detail && <p className="mt-2 whitespace-pre-line text-text-secondary">{item.detail}</p>}
          {checklist && checklist.length > 0 && (
            <ol className="mt-2 space-y-1 text-text-secondary">
              {checklist.map((step, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-text-tertiary">{i + 1}.</span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
          )}
          {item.live && (
            <div className="mt-2.5">
              <Badge tone="waiting">Waiting on you</Badge>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
