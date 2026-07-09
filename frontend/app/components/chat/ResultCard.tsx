"use client";

import { Sparkles } from "lucide-react";
import Avatar from "@/app/components/ui/Avatar";
import Markdown from "@/app/components/ui/Markdown";
import { agentLabel } from "@/app/components/agentTheme";
import { useTypewriter } from "@/hooks/useTypewriter";
import type { ResultItem } from "@/lib/timeline";

// Only "type out" results that just arrived; ones loaded from history show at once.
const FRESH_WINDOW_S = 20;

/** The final deliverable, rendered as a first-class chat bubble. Task-lane
 * answers (`plain`) read like a normal assistant message; the badge is for real
 * project deliverables. Fresh answers reveal progressively, like typing. */
export default function ResultCard({ item }: { item: ResultItem }) {
  const animate = Date.now() / 1000 - item.ts < FRESH_WINDOW_S;
  const { text, done } = useTypewriter(item.markdown, item.key, animate);

  const body = (
    <>
      <Markdown>{text}</Markdown>
      {!done && <span className="typing-caret text-accent">▋</span>}
    </>
  );

  if (item.plain) {
    return (
      <div className="animate-in flex items-start gap-3">
        <Avatar agent="system" />
        <div className="min-w-0 flex-1">
          <div className="max-w-2xl rounded-2xl rounded-tl-sm border border-bubble-assistant-border bg-bubble-assistant-bg px-4 py-3 shadow-sm">
            {item.partial && (
              <p className="mb-1 text-[11px] font-medium text-text-tertiary">
                Partial answer — some steps didn&apos;t finish
              </p>
            )}
            {body}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="animate-in flex items-start gap-3">
      <Avatar agent={item.agent} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-semibold text-text-secondary">{agentLabel(item.agent)}</span>
          <span className="inline-flex items-center gap-1 text-[11px] font-medium text-success">
            <Sparkles size={11} />
            {item.partial ? "Partial result" : "Final result"}
          </span>
        </div>
        <div className="mt-1 max-w-2xl rounded-2xl rounded-tl-sm border border-success/25 bg-bubble-assistant-bg px-4 py-3 shadow-sm">
          {body}
        </div>
      </div>
    </div>
  );
}
