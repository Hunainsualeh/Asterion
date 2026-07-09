"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import type { StageSnapshot, Ticket, TicketOutcome } from "@/lib/api";
import { TICKET_STATUS_STYLES, ticketStatusLabel } from "@/lib/ticketStatus";
import IconButton from "@/app/components/ui/IconButton";
import TypingIndicator from "@/app/components/ui/TypingIndicator";

const LIVE_AGENTS = new Set(["developer", "reviewer", "debugger", "security", "test"]);

/** Live task board: one row per ticket with its current status; expanding a
 * row shows what the task actually produced (summary, files, review/test
 * outcomes) — no more empty click states. */
export default function TasksPanel({
  tickets,
  currentIndex,
  outcomes,
  stage,
  open,
  onClose,
}: {
  tickets: Ticket[];
  currentIndex: number;
  outcomes: Record<string, TicketOutcome>;
  stage: StageSnapshot | null;
  open: boolean;
  onClose: () => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (!open) return null;

  const done = tickets.filter((t) => t.status === "passed").length;
  const liveHeadline = stage && LIVE_AGENTS.has(stage.agent) ? stage.friendly.headline : null;

  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/20 backdrop-blur-[1px] lg:hidden" onClick={onClose} />
      <aside className="fixed inset-y-0 right-0 z-40 flex h-full w-full max-w-sm shrink-0 flex-col border-l border-border bg-surface shadow-xl lg:relative lg:z-auto lg:max-w-xs lg:shadow-none">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3.5">
          <span className="flex-1 text-xs font-semibold uppercase tracking-wider text-text-secondary">
            Tasks — {done}/{tickets.length} done
          </span>
          <IconButton onClick={onClose} aria-label="Close tasks panel">
            <X size={16} />
          </IconButton>
        </div>

        <div className="flex-1 overflow-y-auto scroll-thin px-3 py-3">
          <ul className="flex flex-col gap-2">
            {tickets.map((t, i) => {
              const active = i === currentIndex;
              const outcome = outcomes[t.id];
              const isOpen = expanded === t.id;
              return (
                <li
                  key={t.id}
                  className={`rounded-xl border text-sm transition-colors ${
                    active ? "border-accent/40 bg-accent-soft/40" : "border-border-soft"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => setExpanded(isOpen ? null : t.id)}
                    className="flex w-full items-start justify-between gap-2 p-3 text-left"
                  >
                    <span className="mt-0.5 shrink-0 text-text-tertiary">
                      {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </span>
                    <p className="min-w-0 flex-1 truncate font-medium text-text-primary">{t.title}</p>
                    <span
                      className={`ml-2 shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
                        TICKET_STATUS_STYLES[t.status] ?? TICKET_STATUS_STYLES.pending
                      }`}
                    >
                      {ticketStatusLabel(t.status)}
                    </span>
                  </button>

                  {active && liveHeadline && (
                    <div className="flex items-center gap-2 px-3 pb-2 text-xs text-text-secondary">
                      <span className="truncate">{liveHeadline}</span>
                      <TypingIndicator />
                    </div>
                  )}

                  {isOpen && (
                    <div className="space-y-2.5 border-t border-border-soft px-3 py-2.5 text-xs">
                      {t.description && <p className="text-text-secondary">{t.description}</p>}
                      {outcome?.summary && (
                        <div>
                          <p className="font-semibold text-text-secondary">What was built</p>
                          <p className="whitespace-pre-line text-text-secondary">{outcome.summary}</p>
                        </div>
                      )}
                      {outcome?.files_changed && outcome.files_changed.length > 0 && (
                        <div>
                          <p className="font-semibold text-text-secondary">Files changed</p>
                          <ul className="mt-0.5 space-y-0.5">
                            {outcome.files_changed.slice(0, 12).map((f) => (
                              <li key={f} className="truncate font-mono text-[11px] text-text-tertiary">
                                {f}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {outcome?.auto_test_summary && (
                        <p className="text-text-tertiary">Tests: {outcome.auto_test_summary}</p>
                      )}
                      {outcome?.review_notes && (
                        <p className="text-text-tertiary">Review: {outcome.review_notes.slice(0, 240)}</p>
                      )}
                      {!outcome && t.acceptance_criteria?.length > 0 && (
                        <div>
                          <p className="font-semibold text-text-secondary">Acceptance criteria</p>
                          <ul className="mt-0.5 list-inside list-disc space-y-0.5 text-text-tertiary">
                            {t.acceptance_criteria.map((c, ci) => (
                              <li key={ci}>{c}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {!outcome && <p className="text-text-tertiary">No output recorded yet.</p>}
                    </div>
                  )}
                  <p className="px-3 pb-2 text-xs text-text-tertiary">effort {t.effort}</p>
                </li>
              );
            })}
            {tickets.length === 0 && <li className="px-1 py-2 text-sm text-text-tertiary">No tasks planned yet.</li>}
          </ul>
        </div>
      </aside>
    </>
  );
}
