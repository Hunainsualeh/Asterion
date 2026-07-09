"use client";

import { X } from "lucide-react";
import type { PipelineEvent } from "@/lib/api";
import IconButton from "@/app/components/ui/IconButton";
import ActivityEntry from "./ActivityEntry";

/** The technical log, for anyone curious what's happening under the hood.
 * Opt-in and off by default — the main chat thread is the whole story for
 * everyone else. */
export default function ActivityDrawer({
  events,
  connected,
  open,
  onClose,
}: {
  events: PipelineEvent[];
  connected: boolean;
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/20 backdrop-blur-[1px] lg:hidden" onClick={onClose} />
      <aside className="fixed inset-y-0 right-0 z-40 flex h-full w-full max-w-sm shrink-0 flex-col border-l border-border bg-surface shadow-xl lg:relative lg:z-auto lg:max-w-xs lg:shadow-none">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3.5">
          <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-success" : "bg-text-tertiary"}`} />
          <span className="flex-1 text-xs font-semibold uppercase tracking-wider text-text-secondary">
            Activity log
          </span>
          <IconButton onClick={onClose} aria-label="Close activity log">
            <X size={16} />
          </IconButton>
        </div>
        <p className="border-b border-border-soft px-4 py-2.5 text-xs text-text-tertiary">
          The technical play-by-play — every step each agent takes. Most people don&apos;t need this.
        </p>

        <div className="flex-1 overflow-y-auto scroll-thin px-4 py-4">
          {events.length === 0 && <p className="text-sm text-text-tertiary">Waiting for the pipeline to start...</p>}
          <ol className="relative flex flex-col gap-4 border-l border-border-soft pl-4">
            {events.map((ev, i) => (
              <ActivityEntry key={i} event={ev} />
            ))}
          </ol>
        </div>
      </aside>
    </>
  );
}
