"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Ear, Mic, MicOff, Radio, Square, X } from "lucide-react";
import type { useVoice } from "@/hooks/useVoice";
import VoiceWaveform from "./VoiceWaveform";

type Engine = ReturnType<typeof useVoice>;

const STATUS_TEXT: Record<string, string> = {
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking…",
};

export default function VoiceOverlay({ engine }: { engine: Engine }) {
  const { status } = engine;

  // Background wake-word standby → a small unobtrusive pill. When the mic is
  // parked (tab hidden / idle) the pill says so; it re-arms on your next move.
  if (status === "standby") {
    const paused = engine.standbyPaused;
    return (
      <div className="pointer-events-none fixed bottom-4 left-1/2 z-40 -translate-x-1/2">
        <div className="pointer-events-auto flex items-center gap-2 rounded-full border border-border bg-surface-raised px-3 py-1.5 text-xs text-text-secondary shadow-lg">
          {paused ? (
            <>
              <MicOff size={13} className="text-text-tertiary" />
              Wake word paused — resumes when you’re back
            </>
          ) : (
            <>
              <Ear size={13} className="text-accent" />
              Listening for a wake word
            </>
          )}
        </div>
      </div>
    );
  }

  const activeHud = status === "listening" || status === "thinking" || status === "speaking";
  const isError = status === "denied" || status === "unsupported";

  return (
    <AnimatePresence>
      {(activeHud || isError) && (
        <motion.div
          className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex justify-center px-4 pb-5"
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 24 }}
          transition={{ type: "spring", damping: 26, stiffness: 320 }}
        >
          <div className="pointer-events-auto w-full max-w-lg overflow-hidden rounded-2xl border border-border bg-surface-raised shadow-2xl">
            {isError ? (
              <ErrorBody engine={engine} />
            ) : (
              <div className="p-4">
                <div className="mb-2 flex items-center gap-2">
                  <StatusDot status={status} />
                  <span className="flex-1 text-sm font-medium text-text-primary">{STATUS_TEXT[status]}</span>
                  {status === "speaking" ? (
                    <button
                      onClick={engine.interrupt}
                      className="flex items-center gap-1.5 rounded-lg bg-surface-2 px-2.5 py-1 text-xs font-medium text-text-primary transition-colors hover:bg-border-soft"
                    >
                      <Square size={12} /> Interrupt
                    </button>
                  ) : null}
                  <button
                    onClick={engine.exit}
                    aria-label="Exit voice mode"
                    className="flex h-7 w-7 items-center justify-center rounded-lg text-text-tertiary transition-colors hover:bg-surface-2 hover:text-text-primary"
                  >
                    <X size={15} />
                  </button>
                </div>

                <div className="flex items-center gap-3">
                  <MicOrb status={status} level={engine.level} />
                  <div className="min-w-0 flex-1">
                    <VoiceWaveform analyserRef={engine.analyserRef} active={status === "listening"} />
                  </div>
                </div>

                <div className="mt-2 min-h-[1.5rem]">
                  {status === "speaking" && engine.lastReply ? (
                    <p className="line-clamp-3 text-sm text-text-secondary">
                      <span className="font-medium text-accent">Friday: </span>
                      {engine.lastReply}
                    </p>
                  ) : engine.interim ? (
                    <p className="text-sm text-text-primary">{engine.interim}</p>
                  ) : engine.lastFinal && status !== "listening" ? (
                    <p className="text-sm text-text-secondary">“{engine.lastFinal}”</p>
                  ) : (
                    <p className="text-sm text-text-tertiary">
                      {status === "listening" ? "Go ahead — I'm listening." : "Working on it…"}
                    </p>
                  )}
                </div>

                <p className="mt-2 text-[11px] text-text-tertiary">
                  Say “stop listening” to exit · press Esc to close
                </p>
              </div>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function StatusDot({ status }: { status: string }) {
  const color = status === "thinking" ? "bg-warning" : status === "speaking" ? "bg-accent" : "bg-success";
  return (
    <span className="relative flex h-2.5 w-2.5">
      {status === "listening" && (
        <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${color} opacity-60`} />
      )}
      <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${color}`} />
    </span>
  );
}

function MicOrb({ status, level }: { status: string; level: number }) {
  const scale = status === "listening" ? 1 + Math.min(level, 1) * 0.35 : 1;
  return (
    <div className="relative flex h-12 w-12 shrink-0 items-center justify-center">
      <motion.span
        className="absolute inset-0 rounded-full bg-accent/20"
        animate={{ scale }}
        transition={{ type: "spring", damping: 12, stiffness: 240 }}
      />
      <span className="relative flex h-9 w-9 items-center justify-center rounded-full bg-accent text-accent-foreground">
        {status === "thinking" ? (
          <Radio size={16} className="animate-pulse" />
        ) : status === "speaking" ? (
          <Radio size={16} />
        ) : (
          <Mic size={16} />
        )}
      </span>
    </div>
  );
}

function ErrorBody({ engine }: { engine: Engine }) {
  const denied = engine.status === "denied";
  return (
    <div className="flex items-start gap-3 p-4">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-danger-bg text-danger">
        <MicOff size={17} />
      </span>
      <div className="flex-1">
        <p className="text-sm font-medium text-text-primary">
          {denied ? "Microphone access blocked" : "Voice isn't supported here"}
        </p>
        <p className="mt-0.5 text-xs text-text-secondary">
          {denied
            ? "Allow microphone access in your browser's site settings, then try again."
            : "This browser doesn't support speech recognition. Chrome or Edge work best."}
        </p>
      </div>
      <button
        onClick={engine.exit}
        aria-label="Dismiss"
        className="flex h-7 w-7 items-center justify-center rounded-lg text-text-tertiary hover:bg-surface-2 hover:text-text-primary"
      >
        <X size={15} />
      </button>
    </div>
  );
}
