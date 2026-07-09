"use client";

import { useEffect, useState } from "react";
import { ArrowUp } from "lucide-react";
import ChatInputBar, { type AgentMode } from "@/app/components/ui/ChatInputBar";
import Button from "@/app/components/ui/Button";
import { uploadAttachments, type Ticket } from "@/lib/api";
import { useSettings } from "@/hooks/useSettings";
import { useVoiceControl } from "@/app/components/voice/VoiceProvider";
import type { GateAction } from "@/hooks/usePipelineTimeline";
import type { GateItem } from "@/lib/timeline";

/** Rough client-side file kind for the chip icon (backend does the real work). */
function fileKind(name: string): string {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  if ([".png", ".jpg", ".jpeg", ".webp", ".gif"].includes(ext)) return "image";
  if (ext === ".pdf") return "pdf";
  return "text";
}

/** Round up-arrow send control — same affordance as the home screen. */
function SendButton({ onClick, disabled, busy }: { onClick?: () => void; disabled?: boolean; busy?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label="Send"
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-accent-foreground shadow-sm transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {busy ? (
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
      ) : (
        <ArrowUp size={18} strokeWidth={2.5} />
      )}
    </button>
  );
}

/** The single persistent input at the bottom of the conversation. Its
 * placeholder and action buttons adapt to whatever the pipeline is currently
 * waiting on (a question, an approval, a manual test). When nothing is
 * pending it stays open for free-form follow-up messages — it only locks
 * while the pipeline is actively working. */
export default function ChatComposer({
  activeGate,
  payload,
  busy,
  working,
  error,
  onSubmit,
  onSendChat,
}: {
  activeGate: GateItem | null;
  payload: Record<string, unknown> | undefined;
  busy: boolean;
  /** True while the pipeline is running with nothing pending on the human. */
  working: boolean;
  error: string | null;
  onSubmit: (gate: string, action: GateAction, feedback: string) => void;
  /** Free-form follow-up message; resolves true when accepted. */
  onSendChat: (message: string, opts?: { mode?: AgentMode; attachmentBatchId?: string; tone?: string }) => Promise<boolean>;
}) {
  const { directive } = useSettings();
  const { active: voiceActive } = useVoiceControl();
  const [feedback, setFeedback] = useState("");
  const [mode, setMode] = useState<AgentMode>("auto");
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  useEffect(() => {
    setFeedback("");
  }, [activeGate?.key]);

  // In a live voice session the voice HUD is the only input surface — hide the
  // text composer entirely until the user exits voice mode. Standby (background
  // wake-word listening) is NOT "active", so the text box stays available then.
  if (voiceActive) return null;

  const errorNode = error ? <p className="text-xs text-danger">{error}</p> : undefined;

  if (!activeGate) {
    if (working) {
      return (
        <ChatInputBar
          value={feedback}
          onChange={setFeedback}
          placeholder="Working on it — you can type while you wait..."
          above={errorNode}
          actions={<SendButton disabled />}
        />
      );
    }
    const send = async () => {
      const message = feedback.trim();
      if (!message || busy || uploading) return;
      let attachmentBatchId: string | undefined;
      if (files.length) {
        setUploading(true);
        setUploadError(null);
        try {
          attachmentBatchId = (await uploadAttachments(files)).batch_id;
        } catch (e) {
          setUploadError(e instanceof Error ? e.message : "Couldn't read the attachments.");
          setUploading(false);
          return;
        }
        setUploading(false);
      }
      if (await onSendChat(message, { mode, attachmentBatchId, tone: directive })) {
        setFeedback("");
        setFiles([]);
      }
    };
    const composerError = uploadError ?? error;
    return (
      <ChatInputBar
        value={feedback}
        onChange={setFeedback}
        onSubmitKey={send}
        placeholder="Ask a follow-up, attach files, or pick Deep Research..."
        disabled={busy}
        autoFocus
        above={composerError ? <p className="text-xs text-danger">{composerError}</p> : undefined}
        mode={mode}
        onModeChange={setMode}
        attachments={files.map((f) => ({ name: f.name, kind: fileKind(f.name) }))}
        onFilesSelected={(list) => setFiles((prev) => [...prev, ...Array.from(list)])}
        onRemoveAttachment={(i) => setFiles((prev) => prev.filter((_, idx) => idx !== i))}
        uploading={uploading}
        actions={<SendButton onClick={send} disabled={busy || uploading || !feedback.trim()} busy={busy || uploading} />}
      />
    );
  }

  function submit(action: GateAction) {
    if (!activeGate) return;
    onSubmit(activeGate.gate, action, feedback);
  }

  if (activeGate.gateKind === "clarify") {
    return (
      <ChatInputBar
        value={feedback}
        onChange={setFeedback}
        onSubmitKey={() => feedback.trim() && submit("clarify")}
        placeholder="Your answer..."
        disabled={busy}
        autoFocus
        above={errorNode}
        actions={
          <SendButton onClick={() => submit("clarify")} disabled={busy || !feedback.trim()} busy={busy} />
        }
      />
    );
  }

  if (activeGate.gateKind === "manual_test") {
    const ticket = payload?.ticket as Ticket | undefined;
    return (
      <ChatInputBar
        value={feedback}
        onChange={setFeedback}
        placeholder="If it fails, describe what broke..."
        disabled={busy}
        above={
          <div className="space-y-1">
            {ticket && <p className="text-xs font-medium text-text-secondary">Testing: {ticket.title}</p>}
            {errorNode}
          </div>
        }
        actions={
          <>
            <Button variant="success" onClick={() => submit("pass")} disabled={busy}>
              It works
            </Button>
            <Button variant="danger" onClick={() => submit("fail")} disabled={busy || !feedback.trim()}>
              Something's off
            </Button>
          </>
        }
      />
    );
  }

  // approval
  return (
    <ChatInputBar
      value={feedback}
      onChange={setFeedback}
      placeholder="Feedback (optional; required if you ask for changes)"
      disabled={busy}
      above={errorNode}
      actions={
        <>
          <Button variant="success" onClick={() => submit("approve")} disabled={busy}>
            Looks good
          </Button>
          <Button variant="danger" onClick={() => submit("reject")} disabled={busy || !feedback.trim()}>
            Needs changes
          </Button>
        </>
      }
    />
  );
}
