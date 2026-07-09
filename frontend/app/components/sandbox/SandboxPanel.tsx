"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, Eye, Play, RotateCcw, Square, TerminalSquare, X } from "lucide-react";
import {
  sandboxKill,
  sandboxRun,
  sandboxSessions,
  sandboxStreamUrl,
  type SandboxLogEntry,
  type SandboxSession,
} from "@/lib/api";
import IconButton from "@/app/components/ui/IconButton";
import Button from "@/app/components/ui/Button";

const STREAM_COLOR: Record<SandboxLogEntry["stream"], string> = {
  stdout: "text-text-secondary",
  stderr: "text-danger",
  system: "text-accent",
};

/** Terminal-style sandbox: run commands inside the project's workspace with
 * live streamed output; sessions (including background servers) are listed
 * and killable. */
export default function SandboxPanel({ projectId, open, onClose }: { projectId: string; open: boolean; onClose: () => void }) {
  const [command, setCommand] = useState("");
  const [background, setBackground] = useState(false);
  const [sessions, setSessions] = useState<SandboxSession[]>([]);
  const [log, setLog] = useState<SandboxLogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [previewTick, setPreviewTick] = useState(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  // First live server URL a running session announced (Next/Vite/CRA/etc.).
  const previewUrl = sessions.find((s) => s.status === "running" && s.url)?.url ?? null;

  const refreshSessions = useCallback(async () => {
    try {
      const res = await sandboxSessions(projectId);
      setSessions(res.sessions);
    } catch {
      /* transient */
    }
  }, [projectId]);

  useEffect(() => {
    if (!open) return;
    refreshSessions();
    setLog([]);
    const source = new EventSource(sandboxStreamUrl(projectId));
    const onLog = (e: MessageEvent) => {
      try {
        const entry = JSON.parse(e.data) as SandboxLogEntry;
        setLog((prev) => (prev.length > 2000 ? [...prev.slice(-1500), entry] : [...prev, entry]));
        if (entry.stream === "system") refreshSessions();
      } catch {
        /* ignore malformed frames */
      }
    };
    source.addEventListener("log", onLog);
    return () => {
      source.removeEventListener("log", onLog);
      source.close();
    };
  }, [open, projectId, refreshSessions]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [log.length]);

  // Auto-reveal the preview the moment an app starts serving; hide it again if
  // the server goes away so we never show a dead iframe.
  useEffect(() => {
    if (previewUrl) setShowPreview(true);
    else setShowPreview(false);
  }, [previewUrl]);

  const execute = useCallback(
    async (cmd: string, bg: boolean) => {
      const c = cmd.trim();
      if (!c) return;
      setError(null);
      try {
        await sandboxRun(projectId, c, bg ? 1800 : 120, bg);
        refreshSessions();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Command was rejected.");
      }
    },
    [projectId, refreshSessions],
  );

  async function run() {
    const cmd = command.trim();
    if (!cmd) return;
    await execute(cmd, background);
    setCommand("");
  }

  // One-click flows so "run it like a real app" doesn't require typing. Dev
  // servers run in the background (long-lived); installs run in the foreground.
  const PRESETS: { label: string; cmd: string; bg: boolean }[] = [
    { label: "npm install", cmd: "npm install", bg: false },
    { label: "Install & run (npm)", cmd: "npm install && npm run dev", bg: true },
    { label: "npm run dev", cmd: "npm run dev", bg: true },
    { label: "pip install", cmd: "pip install -r requirements.txt", bg: false },
  ];

  async function kill(sid: string) {
    try {
      await sandboxKill(projectId, sid);
      refreshSessions();
    } catch {
      refreshSessions();
    }
  }

  if (!open) return null;

  const running = sessions.filter((s) => s.status === "running");

  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/20 backdrop-blur-[1px] lg:hidden" onClick={onClose} />
      <aside className="fixed inset-y-0 right-0 z-40 flex h-full w-full max-w-2xl shrink-0 flex-col border-l border-border bg-surface shadow-xl lg:relative lg:z-auto lg:max-w-xl lg:shadow-none">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3.5">
          <span className="flex-1 text-xs font-semibold uppercase tracking-wider text-text-secondary">Sandbox</span>
          {previewUrl && (
            <div className="flex overflow-hidden rounded-lg border border-border">
              <button
                type="button"
                onClick={() => setShowPreview(true)}
                className={`inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium transition-colors ${
                  showPreview ? "bg-accent-soft text-accent" : "text-text-secondary hover:bg-surface-2"
                }`}
              >
                <Eye size={11} /> Preview
              </button>
              <button
                type="button"
                onClick={() => setShowPreview(false)}
                className={`inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium transition-colors ${
                  !showPreview ? "bg-accent-soft text-accent" : "text-text-secondary hover:bg-surface-2"
                }`}
              >
                <TerminalSquare size={11} /> Logs
              </button>
            </div>
          )}
          <IconButton onClick={onClose} aria-label="Close sandbox panel">
            <X size={16} />
          </IconButton>
        </div>

        {previewUrl && (
          <div className="flex items-center gap-2 border-b border-border-soft bg-success/5 px-4 py-2 text-xs">
            <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-success" />
            <span className="min-w-0 flex-1 truncate text-text-secondary">
              App running at <span className="font-mono text-text-primary">{previewUrl}</span>
            </span>
            {showPreview && (
              <button
                type="button"
                onClick={() => setPreviewTick((t) => t + 1)}
                className="inline-flex items-center gap-1 text-text-tertiary transition-colors hover:text-text-primary"
                aria-label="Reload preview"
              >
                <RotateCcw size={12} /> Reload
              </button>
            )}
            <a
              href={previewUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 font-medium text-accent hover:underline"
            >
              <ExternalLink size={12} /> Open app
            </a>
          </div>
        )}

        {running.length > 0 && (
          <div className="border-b border-border-soft px-4 py-2">
            {running.map((s) => (
              <div key={s.id} className="flex items-center gap-2 py-0.5 text-xs">
                <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-success" />
                <span className="min-w-0 flex-1 truncate font-mono text-text-secondary">{s.command}</span>
                <button
                  type="button"
                  onClick={() => kill(s.id)}
                  className="inline-flex items-center gap-1 text-danger hover:underline"
                >
                  <Square size={10} /> stop
                </button>
              </div>
            ))}
          </div>
        )}

        {showPreview && previewUrl ? (
          <iframe
            key={`${previewUrl}#${previewTick}`}
            src={previewUrl}
            title="App preview"
            sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-modals"
            className="min-h-0 flex-1 bg-white"
          />
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto scroll-thin bg-black/[0.03] px-4 py-3 font-mono text-[11px] leading-relaxed dark:bg-white/[0.03]">
            {log.length === 0 && <p className="text-text-tertiary">No output yet — run a command below.</p>}
            {log.map((entry, i) => (
              <div key={i} className={`whitespace-pre-wrap break-all ${STREAM_COLOR[entry.stream]}`}>
                {entry.line}
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        )}

        <div className="border-t border-border px-4 py-3">
          {error && <p className="pb-1.5 text-xs text-danger">{error}</p>}
          <div className="mb-2 flex flex-wrap gap-1.5">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                onClick={() => execute(p.cmd, p.bg)}
                title={p.cmd}
                className="rounded-full border border-border bg-surface px-2.5 py-1 font-mono text-[10px] text-text-secondary transition-colors hover:border-accent/40 hover:text-text-primary"
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <input
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
              placeholder="e.g. python main.py · npm test · pip install -r requirements.txt"
              spellCheck={false}
              className="min-w-0 flex-1 rounded-lg border border-border bg-transparent px-3 py-2 font-mono text-xs text-text-primary outline-none placeholder:text-text-tertiary focus:border-accent/50"
            />
            <Button onClick={run} disabled={!command.trim()}>
              <span className="inline-flex items-center gap-1.5">
                <Play size={13} /> Run
              </span>
            </Button>
          </div>
          <label className="mt-1.5 flex items-center gap-1.5 text-[11px] text-text-tertiary">
            <input type="checkbox" checked={background} onChange={(e) => setBackground(e.target.checked)} />
            Keep running in the background (dev servers)
          </label>
        </div>
      </aside>
    </>
  );
}
