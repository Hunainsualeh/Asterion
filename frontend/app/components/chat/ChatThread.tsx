"use client";

import { useEffect, useRef } from "react";
import Avatar from "@/app/components/ui/Avatar";
import Spinner from "@/app/components/ui/Spinner";
import TypingIndicator from "@/app/components/ui/TypingIndicator";
import { agentLabel } from "@/app/components/agentTheme";
import type { Ticket } from "@/lib/api";
import type { PendingSubmission } from "@/hooks/usePipelineTimeline";
import type { LiveProgress, TimelineItem } from "@/lib/timeline";
import ChatMessage from "./ChatMessage";
import ClarifyCard from "./ClarifyCard";
import GateCard from "./GateCard";
import DocumentCard from "./DocumentCard";
import TicketsCard from "./TicketsCard";
import ErrorCard from "./ErrorCard";
import UserReplyBubble from "./UserReplyBubble";
import ResultCard from "./ResultCard";
import DagProgressCard from "./DagProgressCard";

export default function ChatThread({
  items,
  interruptPayload,
  pendingSubmission,
  working,
  progress,
  onRetry,
  retryBusy,
  onSendChat,
}: {
  items: TimelineItem[];
  interruptPayload: Record<string, unknown> | undefined;
  pendingSubmission: PendingSubmission | null;
  working: boolean;
  progress: LiveProgress;
  onRetry: () => void;
  retryBusy: boolean;
  onSendChat: (message: string) => Promise<boolean>;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const lastGateIndex = items.map((it) => it.type).lastIndexOf("gate");

  useEffect(() => {
    // Scroll only the thread's own container — scrollIntoView would also
    // scroll every ancestor, dragging the whole app shell up with it.
    const el = scrollerRef.current;
    el?.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [items.length, working]);

  if (items.length === 0 && !working) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-text-tertiary">Starting up...</p>
      </div>
    );
  }

  return (
    <div ref={scrollerRef} className="relative min-h-0 flex-1 overflow-y-auto scroll-thin px-4 py-6 sm:px-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-5">
        {items.map((item, i) => {
          switch (item.type) {
            case "narration":
              return (
                <ChatMessage
                  key={item.key}
                  agent={item.agent}
                  headline={item.headline}
                  detail={item.detail}
                  tone={item.tone}
                  ts={item.ts}
                />
              );
            case "gate": {
              const checklist = item.live ? (interruptPayload?.checklist as string[] | undefined) : undefined;
              const showEcho = i === lastGateIndex && pendingSubmission?.gate === item.gate;
              return (
                <div key={item.key} className="flex flex-col gap-3">
                  <GateCard item={item} checklist={checklist} />
                  {showEcho && pendingSubmission && (
                    <>
                      <UserReplyBubble action={pendingSubmission.action} feedback={pendingSubmission.feedback} />
                      <div className="ml-11 flex items-center gap-2 text-xs text-text-tertiary">
                        <Spinner size={12} />
                        Sending that to the pipeline...
                      </div>
                    </>
                  )}
                </div>
              );
            }
            case "document":
              return <DocumentCard key={item.key} title={item.title} doc={item.doc} qa={item.qa} />;
            case "tickets":
              return <TicketsCard key={item.key} tickets={item.tickets as Ticket[]} />;
            case "error":
              return <ErrorCard key={item.key} item={item} onRetry={onRetry} busy={retryBusy} />;
            case "result":
              return <ResultCard key={item.key} item={item} />;
            case "dag":
              return <DagProgressCard key={item.key} dag={item.dag} />;
            case "clarify":
              return <ClarifyCard key={item.key} item={item} onSend={onSendChat} busy={retryBusy} />;
            case "user":
              return (
                <div key={item.key} className="animate-in flex justify-end">
                  <div className="max-w-xl rounded-2xl rounded-tr-sm bg-bubble-user-bg px-4 py-2.5 text-sm leading-relaxed text-bubble-user-text shadow-sm">
                    <p className="whitespace-pre-line">{item.text}</p>
                  </div>
                </div>
              );
            default:
              return null;
          }
        })}

        {working && !pendingSubmission && (
          <div className="flex items-start gap-3">
            <Avatar agent={progress.agent} />
            <div className="min-w-0">
              {progress.agent !== "system" && (
                <p className="mb-0.5 text-xs font-semibold text-text-secondary">{agentLabel(progress.agent)}</p>
              )}
              <div className="flex items-center gap-2 rounded-2xl rounded-tl-sm border border-bubble-assistant-border bg-bubble-assistant-bg px-4 py-2.5 text-sm text-text-secondary shadow-sm">
                <span>{progress.headline}</span>
                <TypingIndicator />
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  );
}
