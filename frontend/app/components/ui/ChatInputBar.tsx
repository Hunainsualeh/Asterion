"use client";

import { ChevronDown, FileText, Image as ImageIcon, Paperclip, Sparkles, Telescope, X } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import VoiceButton from "@/app/components/voice/VoiceButton";

export type AgentMode = "auto" | "research";

export interface AttachmentChip {
  name: string;
  kind: string;
}

const MODE_LABEL: Record<AgentMode, string> = { auto: "Auto", research: "Deep Research" };
const MODE_HINT: Record<AgentMode, string> = {
  auto: "Pick the right agent automatically",
  research: "Multi-step research → full report",
};

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,.gif,.txt,.md,.markdown,.csv,.json,.log,.yaml,.yml,.xml,.html,.htm";

export default function ChatInputBar({
  value,
  onChange,
  onSubmitKey,
  placeholder,
  disabled,
  autoFocus,
  minRows = 1,
  actions,
  above,
  mode,
  onModeChange,
  attachments,
  onFilesSelected,
  onRemoveAttachment,
  uploading,
}: {
  value: string;
  onChange: (value: string) => void;
  /** Called on Enter (without Shift). Omit to disable submit-on-Enter. */
  onSubmitKey?: () => void;
  placeholder?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  minRows?: number;
  /** Buttons rendered to the right of the textarea. */
  actions?: ReactNode;
  /** Content rendered above the input row, inside the same docked bar. */
  above?: ReactNode;
  /** Controlled agent/mode selector. Omit both to hide the selector. */
  mode?: AgentMode;
  onModeChange?: (mode: AgentMode) => void;
  /** Attachment support. Omit `onFilesSelected` to hide the attach button. */
  attachments?: AttachmentChip[];
  onFilesSelected?: (files: FileList) => void;
  onRemoveAttachment?: (index: number) => void;
  uploading?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [isMultiline, setIsMultiline] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const showModes = onModeChange !== undefined;
  const showAttach = onFilesSelected !== undefined;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Measure the natural height, then animate from the previous height to
    // it — without the restore+reflow step the height would snap.
    const prev = el.style.height;
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, 240);
    setIsMultiline(el.scrollHeight > 44);
    if (prev && prev !== `${next}px`) {
      el.style.height = prev;
      void el.offsetHeight; // flush layout so the transition has a start value
    }
    el.style.height = `${next}px`;
  }, [value]);

  useEffect(() => {
    if (!menuOpen) return;
    function onClickAway(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", onClickAway);
    return () => document.removeEventListener("mousedown", onClickAway);
  }, [menuOpen]);

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && onSubmitKey) {
      e.preventDefault();
      onSubmitKey();
    }
  }

  return (
    <div className="bg-bg px-4 pb-4 pt-3 sm:px-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-2">
        {above}

        {attachments && attachments.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {attachments.map((a, i) => (
              <div
                key={`${a.name}-${i}`}
                className="group/att flex items-center gap-2 rounded-xl border border-border bg-surface-2 py-1.5 pl-1.5 pr-2.5"
              >
                <div
                  className={`flex h-9 w-7 shrink-0 items-center justify-center rounded-md border border-border-soft bg-bg text-text-secondary ${
                    uploading ? "scan-rect" : ""
                  }`}
                >
                  {a.kind === "image" ? <ImageIcon size={15} /> : <FileText size={15} />}
                </div>
                <div className="min-w-0">
                  <div className="max-w-[150px] truncate text-xs font-medium text-text-primary">{a.name}</div>
                  <div className="text-[10px] uppercase tracking-wide text-text-tertiary">
                    {uploading ? "reading…" : a.kind}
                  </div>
                </div>
                {onRemoveAttachment && !uploading && (
                  <button
                    type="button"
                    onClick={() => onRemoveAttachment(i)}
                    aria-label={`Remove ${a.name}`}
                    className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-text-tertiary opacity-0 transition-opacity hover:bg-surface hover:text-text-primary group-hover/att:opacity-100"
                  >
                    <X size={12} />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {mode && mode !== "auto" && (
          <div className="flex items-center gap-1.5 self-start rounded-full bg-accent-soft px-3 py-1 text-xs font-medium text-accent">
            <Telescope size={12} /> {MODE_LABEL[mode]}
          </div>
        )}

        <div
          // Numeric radii (not rounded-full's 9999px) so the pill→rounded-
          // square morph interpolates visibly instead of snapping at the end.
          style={{ borderRadius: isMultiline ? 16 : 24 }}
          className={`flex gap-2 border border-border bg-surface-2 shadow-sm transition-all duration-300 ease-out focus-within:border-accent/50 focus-within:shadow-md ${
            isMultiline ? "items-end py-3 pl-4 pr-3" : "items-center py-2 pl-4 pr-3"
          }`}
        >
          <div className={`flex shrink-0 items-center ${isMultiline ? "pb-0.5" : ""}`}>
            <VoiceButton size={19} className="h-8 w-8" />
          </div>

          {(showModes || showAttach) && (
            <div className={`relative flex shrink-0 items-center gap-1.5 ${isMultiline ? "pb-0.5" : ""}`} ref={menuRef}>
              {showAttach && (
                <>
                  <input
                    ref={fileRef}
                    type="file"
                    multiple
                    accept={ACCEPT}
                    className="hidden"
                    onChange={(e) => {
                      if (e.target.files && e.target.files.length) onFilesSelected!(e.target.files);
                      e.target.value = "";
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => fileRef.current?.click()}
                    disabled={disabled}
                    className="flex items-center justify-center text-text-secondary transition-colors hover:text-text-primary disabled:opacity-40"
                    aria-label="Attach files"
                  >
                    <Paperclip size={19} />
                  </button>
                </>
              )}

              {showModes && (
                <>
                  <button
                    type="button"
                    onClick={() => setMenuOpen((v) => !v)}
                    className="flex items-center gap-0.5 text-text-secondary transition-colors hover:text-text-primary"
                    aria-label="Choose agent"
                  >
                    {mode === "research" ? <Telescope size={18} /> : <Sparkles size={18} />}
                    <ChevronDown size={13} />
                  </button>
                  {menuOpen && (
                    <div className="absolute bottom-11 left-0 z-10 w-64 rounded-xl border border-border bg-surface-raised p-1.5 shadow-lg">
                      {(["auto", "research"] as AgentMode[]).map((m) => (
                        <button
                          key={m}
                          type="button"
                          onClick={() => {
                            onModeChange!(m);
                            setMenuOpen(false);
                          }}
                          className={`flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-surface-2 ${
                            mode === m ? "text-accent" : "text-text-primary"
                          }`}
                        >
                          {m === "research" ? (
                            <Telescope size={16} className="mt-0.5 shrink-0" />
                          ) : (
                            <Sparkles size={16} className="mt-0.5 shrink-0" />
                          )}
                          <span className="flex flex-col">
                            <span className="text-sm font-medium">{MODE_LABEL[m]}</span>
                            <span className="text-[11px] text-text-tertiary">{MODE_HINT[m]}</span>
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          <textarea
            ref={ref}
            style={{ transition: "height 200ms ease-out" }}
            className="chat-textarea max-h-[240px] flex-1 resize-none bg-transparent py-0.5 text-sm leading-relaxed text-text-primary placeholder:text-text-tertiary focus:outline-none"
            rows={minRows}
            placeholder={placeholder}
            value={value}
            disabled={disabled}
            autoFocus={autoFocus}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <div className={`flex shrink-0 gap-1 ${isMultiline ? "items-end pb-0.5" : "items-center"}`}>{actions}</div>
        </div>
      </div>
    </div>
  );
}
