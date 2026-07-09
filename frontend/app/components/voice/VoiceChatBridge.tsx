"use client";

import { useEffect, useRef } from "react";
import type { PipelineEvent } from "@/lib/api";

const AUTOSPEAK_KEY = "asterion:voice-autospeak";

/** Lives inside the pipeline view (which owns the SSE stream). It receives
 * voice utterances, sends them to this chat, and speaks the AI's reply back
 * to the voice engine — plus wires "stop/start agent" voice commands to the
 * run controls. Keeping this here means voice reuses the exact same chat path
 * as typing, so both surfaces stay in sync. */
export default function VoiceChatBridge({
  projectId,
  events,
  sendChat,
  onStop,
  onStart,
}: {
  projectId: string;
  events: PipelineEvent[];
  sendChat: (text: string) => void;
  onStop: () => void;
  onStart: () => void;
}) {
  const expecting = useRef(false);
  const lastSpokenId = useRef<string | null>(null);

  // A project started by voice should speak its first answer aloud.
  useEffect(() => {
    try {
      if (sessionStorage.getItem(AUTOSPEAK_KEY) === projectId) {
        expecting.current = true;
        sessionStorage.removeItem(AUTOSPEAK_KEY);
      }
    } catch {
      /* ignore */
    }
  }, [projectId]);

  // Voice utterance → send into this chat.
  useEffect(() => {
    const onUtterance = (e: Event) => {
      const detail = (e as CustomEvent).detail as { text?: string; pid?: string };
      if (!detail?.text) return;
      if (detail.pid && detail.pid !== projectId) return;
      expecting.current = true;
      sendChat(detail.text);
    };
    const onPageCommand = (e: Event) => {
      const action = ((e as CustomEvent).detail as { action?: string })?.action;
      if (action === "stop_agent") onStop();
      if (action === "start_agent") onStart();
    };
    window.addEventListener("asterion:voice-utterance", onUtterance);
    window.addEventListener("asterion:voice-page-command", onPageCommand);
    return () => {
      window.removeEventListener("asterion:voice-utterance", onUtterance);
      window.removeEventListener("asterion:voice-page-command", onPageCommand);
    };
  }, [projectId, sendChat, onStop, onStart]);

  // When a fresh result arrives and we're expecting one, speak it.
  useEffect(() => {
    if (!expecting.current) return;
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i];
      if (ev.kind !== "result") continue;
      const id = ev.id ?? `${ev.kind}-${ev.ts}`;
      if (id === lastSpokenId.current) break;
      const text = String((ev.data as Record<string, unknown>)?.result ?? "");
      if (text) {
        lastSpokenId.current = id;
        expecting.current = false;
        window.dispatchEvent(new CustomEvent("asterion:voice-reply", { detail: { text } }));
      }
      break;
    }
  }, [events]);

  return null;
}
