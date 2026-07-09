"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  answerClarification,
  approveGate,
  cancelProject,
  getProject,
  retryProject,
  sendMessage as sendMessageApi,
  submitManualTest,
  type PipelineEvent,
  type ProjectDetail,
} from "@/lib/api";
import { useEventStream } from "@/lib/sse";

const REFRESH_ON: ReadonlySet<string> = new Set([
  "gate",
  "awaiting_input",
  "done",
  "error",
  "running",
  "project_updated",
  "result",
  "ticket_done",
  "cancelled",
  "dag_started",
  "dag_finished",
  "user_message",
]);
// SSE drives real-time updates; this is only a safety net for a missed/dropped event.
const POLL_MS = 15000;

export type GateAction = "clarify" | "approve" | "reject" | "pass" | "fail";

export interface PendingSubmission {
  gate: string;
  /** The exact pause this submission answered — NOT just the gate name.
   * The same gate can legitimately fire again (clarify loops, rejections);
   * comparing ids is what re-enables the composer for the new pause. */
  interruptId: string;
  action: GateAction;
  feedback: string;
}

export function usePipelineTimeline(projectId: string) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pendingSubmission, setPendingSubmission] = useState<PendingSubmission | null>(null);
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const seenIds = useRef<Set<string>>(new Set());

  // Hard-reset all stream state when switching projects, so a reused
  // component instance can't leak one project's timeline into another.
  useEffect(() => {
    setEvents([]);
    setPendingSubmission(null);
    setSubmitError(null);
    seenIds.current = new Set();
  }, [projectId]);

  const refresh = useCallback(async () => {
    try {
      const detail = await getProject(projectId);
      setProject(detail);
      setLoadError(null);
      setPendingSubmission((current) => {
        if (!current) return null;
        // Keep the "sending..." echo only while the exact pause we answered
        // is still the pending one; any new pause (even the same gate name)
        // or no pause at all clears it and re-enables the composer.
        return detail.interrupt?.interrupt_id === current.interruptId ? current : null;
      });
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load project");
    }
  }, [projectId]);

  useEffect(() => {
    refresh();
    let interval: ReturnType<typeof setInterval> | null = null;
    function start() {
      if (interval || document.hidden) return;
      interval = setInterval(refresh, POLL_MS);
    }
    function stop() {
      if (!interval) return;
      clearInterval(interval);
      interval = null;
    }
    function onVisibilityChange() {
      if (document.hidden) stop();
      else {
        start();
        refresh(); // catch up on anything missed while backgrounded
      }
    }
    start();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [refresh]);

  const { connected } = useEventStream(
    projectId,
    useCallback(
      (event: PipelineEvent) => {
        // Belt-and-braces dedupe by stream id (sse.ts also dedupes per
        // connection; this survives reconnect churn across connections).
        if (event.id) {
          if (seenIds.current.has(event.id)) return;
          seenIds.current.add(event.id);
        }
        setEvents((prev) => [...prev, event]);
        if (REFRESH_ON.has(event.kind)) refresh();
      },
      [refresh],
    ),
  );

  async function submit(gate: string, action: GateAction, feedback: string) {
    const interruptId = project?.interrupt?.interrupt_id ?? "";
    setBusy(true);
    setSubmitError(null);
    try {
      if (action === "clarify") await answerClarification(projectId, feedback, interruptId);
      else if (action === "approve" || action === "reject") await approveGate(projectId, action, feedback, interruptId);
      else await submitManualTest(projectId, action, feedback, interruptId);

      setPendingSubmission({ gate, interruptId, action, feedback });
      refresh();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Stale gate or busy pipeline — resync instead of leaving a dead form.
        setSubmitError("That request was already handled — catching up...");
        refresh();
      } else {
        setSubmitError(err instanceof ApiError ? err.message : "Something went wrong sending that — try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function sendChat(
    message: string,
    opts?: { mode?: "auto" | "research"; attachmentBatchId?: string; tone?: string },
  ): Promise<boolean> {
    setBusy(true);
    setSubmitError(null);
    try {
      await sendMessageApi(projectId, message, opts);
      refresh();
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setSubmitError("Still working on the last request — give it a second.");
        refresh();
      } else {
        setSubmitError(err instanceof ApiError ? err.message : "Couldn't send that — try again.");
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    setBusy(true);
    setSubmitError(null);
    try {
      await retryProject(projectId);
      refresh();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : "Couldn't retry — try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    setBusy(true);
    setSubmitError(null);
    try {
      await cancelProject(projectId);
      refresh();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : "Couldn't stop the run.");
    } finally {
      setBusy(false);
    }
  }

  return {
    project,
    events,
    connected,
    loadError,
    pendingSubmission,
    busy,
    submitError,
    submit,
    sendChat,
    retry,
    cancel,
    refresh,
  };
}
