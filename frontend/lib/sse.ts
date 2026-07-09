"use client";

import { useEffect, useRef, useState } from "react";
import { eventsUrl, type PipelineEvent } from "./api";

// Every `kind` the backend ever publishes (grep publish_event callers in
// app/orchestration + app/dag). Named SSE events need an explicit listener
// each — a kind missing here is silently dropped by EventSource, which is
// exactly how `digest`/`audit` events used to vanish.
const EVENT_KINDS = [
  "running",
  "error",
  "user_message",
  "awaiting_input",
  "agent_started",
  "agent_message",
  "gate",
  "done",
  "project_updated",
  "digest",
  "audit",
  "result",
  "ticket_done",
  "cancelled",
  "tool_call",
  "dag_started",
  "dag_finished",
  "node_started",
  "node_finished",
  "node_failed",
  "node_retry",
  "node_skipped",
  "ui_action",
] as const;

/** Subscribes to a project's live SSE event stream for as long as the component
 * is mounted. The server sets each frame's SSE id to its Redis stream entry id:
 * reconnects resume from Last-Event-ID (no full replay) and we dedupe on it so
 * a replayed frame can never double-render a chat bubble. */
export function useEventStream(projectId: string | null, onEvent: (event: PipelineEvent) => void) {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!projectId) return;

    const seen = new Set<string>();
    const source = new EventSource(eventsUrl(projectId));
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);

    const listeners = EVENT_KINDS.map((kind) => {
      const listener = (e: MessageEvent) => {
        try {
          const eventId = e.lastEventId || "";
          if (eventId) {
            if (seen.has(eventId)) return;
            seen.add(eventId);
          }
          const parsed = JSON.parse(e.data) as PipelineEvent;
          if (eventId && !parsed.id) parsed.id = eventId;
          handlerRef.current(parsed);
        } catch {
          // ignore malformed frames
        }
      };
      source.addEventListener(kind, listener);
      return { kind, listener };
    });

    return () => {
      for (const { kind, listener } of listeners) source.removeEventListener(kind, listener);
      source.close();
      setConnected(false);
    };
  }, [projectId]);

  return { connected };
}
