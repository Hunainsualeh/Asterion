"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, RefreshCw, Sparkles } from "lucide-react";
import { startProject, uploadAttachments } from "@/lib/api";
import { useSettings } from "@/hooks/useSettings";
import ChatInputBar, { type AgentMode } from "./components/ui/ChatInputBar";
import UpcomingTasks from "./components/tasks/UpcomingTasks";
import { useVoiceControl } from "./components/voice/VoiceProvider";

function fileKind(name: string): string {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  if ([".png", ".jpg", ".jpeg", ".webp", ".gif"].includes(ext)) return "image";
  if (ext === ".pdf") return "pdf";
  return "text";
}

const EXAMPLE_POOL = [
  "A tool that tracks my reading list and reminds me to finish books I've started",
  "A CLI that converts between Celsius and Fahrenheit",
  "A small web app for splitting a restaurant bill between friends",
  "A Pomodoro timer with task tracking and a daily focus summary",
  "A markdown-based personal wiki with full-text search",
  "A habit tracker with streaks and weekly charts",
  "A URL shortener with click analytics",
  "A recipe manager that generates a shopping list from selected meals",
  "A kanban board with drag-and-drop and localStorage persistence",
  "A weather dashboard for a few saved cities",
  "A flashcard app with spaced repetition",
  "A budget tracker that categorizes expenses and shows monthly trends",
  "A REST API for a simple blog with posts and comments",
  "An expense-splitting group ledger for roommates",
];

function pick3(): string[] {
  return [...EXAMPLE_POOL].sort(() => Math.random() - 0.5).slice(0, 3);
}

export default function Home() {
  const router = useRouter();
  const { directive } = useSettings();
  const { active: voiceActive } = useVoiceControl();
  const [idea, setIdea] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<AgentMode>("auto");
  const [files, setFiles] = useState<File[]>([]);
  // Seed with a fixed slice for SSR, then randomize on mount (avoids a hydration mismatch).
  const [examples, setExamples] = useState<string[]>(EXAMPLE_POOL.slice(0, 3));

  useEffect(() => {
    setExamples(pick3());
  }, []);

  async function submit() {
    if (!idea.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      let attachmentBatchId: string | undefined;
      if (files.length) {
        attachmentBatchId = (await uploadAttachments(files)).batch_id;
      }
      const { project_id } = await startProject(idea.trim(), { mode, attachmentBatchId, tone: directive });
      router.push(`/pipeline/${project_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start project");
      setSubmitting(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto px-6">
        <div className="w-full max-w-2xl text-center">
          <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent text-accent-foreground shadow-lg shadow-accent/20">
            <Sparkles size={26} strokeWidth={2.25} />
          </div>
          <h1 className="text-3xl font-semibold tracking-tight text-text-primary">What are we building today?</h1>
          <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-text-secondary">
            Describe your idea in plain language. I&apos;ll ask questions where I need to, show you a plan, and check
            in with you before every big step — no experience required.
          </p>

          <div className="mt-8 flex flex-wrap items-center justify-center gap-2">
            {examples.map((example) => (
              <button
                key={example}
                onClick={() => setIdea(example)}
                className="rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-text-secondary transition-colors hover:border-accent/40 hover:text-text-primary"
              >
                {example}
              </button>
            ))}
            <button
              onClick={() => setExamples(pick3())}
              aria-label="Shuffle examples"
              className="flex h-7 w-7 items-center justify-center rounded-full border border-border bg-surface text-text-tertiary transition-colors hover:border-accent/40 hover:text-text-primary"
            >
              <RefreshCw size={13} />
            </button>
          </div>

          <UpcomingTasks />
        </div>
      </div>

      {/* Hidden while a live voice session owns the screen; restored on exit. */}
      {!voiceActive && (
        <ChatInputBar
          value={idea}
          onChange={setIdea}
          onSubmitKey={submit}
          placeholder="Ask anything"
          disabled={submitting}
          autoFocus
          minRows={1}
          above={error ? <p className="text-xs text-danger">{error}</p> : undefined}
          mode={mode}
          onModeChange={setMode}
          attachments={files.map((f) => ({ name: f.name, kind: fileKind(f.name) }))}
          onFilesSelected={(list) => setFiles((prev) => [...prev, ...Array.from(list)])}
          onRemoveAttachment={(i) => setFiles((prev) => prev.filter((_, idx) => idx !== i))}
          uploading={submitting && files.length > 0}
          actions={
            <button
              onClick={submit}
              disabled={!idea.trim() || submitting}
              className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-accent-foreground shadow-sm transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              ) : (
                <ArrowRight size={18} />
              )}
            </button>
          }
        />
      )}
    </div>
  );
}
