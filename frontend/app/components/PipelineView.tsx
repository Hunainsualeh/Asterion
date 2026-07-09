"use client";

import { useState } from "react";
import { FolderOpen, ListChecks, ListTree, Square, TerminalSquare } from "lucide-react";
import { usePipelineTimeline } from "@/hooks/usePipelineTimeline";
import { buildTimeline, isWorking, latestProgress, type GateItem } from "@/lib/timeline";
import ChatThread from "./chat/ChatThread";
import ChatComposer from "./chat/ChatComposer";
import ActivityDrawer from "./activity/ActivityDrawer";
import TasksPanel from "./tasks/TasksPanel";
import FilesPanel from "./files/FilesPanel";
import SandboxPanel from "./sandbox/SandboxPanel";
import ActionExecutor from "./control/ActionExecutor";
import VoiceChatBridge from "./voice/VoiceChatBridge";
import IconButton from "./ui/IconButton";
import { VOICE_ENABLED } from "@/lib/voice";

type SidePanel = "activity" | "tasks" | "files" | "sandbox" | null;

export default function PipelineView({ projectId }: { projectId: string }) {
  const { project, events, connected, loadError, pendingSubmission, busy, submitError, submit, sendChat, retry, cancel } =
    usePipelineTimeline(projectId);
  const [sidePanel, setSidePanel] = useState<SidePanel>(null);

  if (loadError && !project) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <p className="text-sm text-danger">Couldn&apos;t load this project: {loadError}</p>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-text-tertiary">Loading...</p>
      </div>
    );
  }

  const submittingInterruptId = pendingSubmission?.interruptId ?? null;
  const items = buildTimeline(events, project, submittingInterruptId);
  const working = isWorking(project, Boolean(pendingSubmission));
  const progress = latestProgress(project);
  const activeGate = (items.find((it) => it.type === "gate" && it.live) as GateItem | undefined) ?? null;
  const tickets = project.state.tickets ?? [];
  const ticketIndex = Math.min(project.state.current_ticket_index ?? 0, Math.max(tickets.length - 1, 0));
  const ticketsDone = tickets.filter((t) => t.status === "passed").length;

  const togglePanel = (panel: Exclude<SidePanel, null>) => setSidePanel((v) => (v === panel ? null : panel));

  return (
    <div className="flex h-full min-w-0 flex-1">
      {/* Runs conversational system-control actions (open settings, new chat,
          delete chat, switch theme) emitted as ui_action events on this stream. */}
      <ActionExecutor events={events} projectId={projectId} />
      {/* Bridges voice utterances into this chat and speaks the AI reply back.
          Off with the rest of the voice stack (see lib/voice.ts) — with no
          engine to emit them, its window listeners would never fire anyway. */}
      {VOICE_ENABLED && (
        <VoiceChatBridge
          projectId={projectId}
          events={events}
          sendChat={(text) => sendChat(text)}
          onStop={cancel}
          onStart={retry}
        />
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        {/* One slim bar: connection dot, the task strip (or a quiet title
            when there are no tasks yet), run control, panel toggles. */}
        <header className="flex items-center gap-3 px-4 py-2.5 sm:px-6">
          <span className={`h-2 w-2 shrink-0 rounded-full ${connected ? "bg-success" : "bg-text-tertiary"}`} />
          {tickets.length > 0 ? (
            <div className="flex min-w-0 flex-1 items-center gap-3 text-xs text-text-secondary">
              <span className="shrink-0 font-medium text-text-primary">
                Task {ticketIndex + 1} of {tickets.length}
              </span>
              <span className="min-w-0 flex-1 truncate">{tickets[ticketIndex]?.title}</span>
              <div className="hidden h-1.5 w-20 shrink-0 overflow-hidden rounded-full bg-border-soft sm:block">
                <div
                  className="h-full rounded-full bg-success transition-all"
                  style={{ width: `${(ticketsDone / tickets.length) * 100}%` }}
                />
              </div>
            </div>
          ) : (
            <span className="min-w-0 flex-1 truncate text-xs text-text-tertiary">
              {project.title || project.idea}
            </span>
          )}
          {project.running && (
            <IconButton onClick={cancel} aria-label="Stop the current run">
              <Square size={14} className="text-danger" />
            </IconButton>
          )}
          {/* Panel toggles grouped into a visible toolbar pill — discoverable
              without a full header bar/divider. */}
          <div className="flex items-center gap-0.5 rounded-xl border border-border bg-surface-2/70 p-1 shadow-sm">
            {tickets.length > 0 && (
              <IconButton
                active={sidePanel === "tasks"}
                onClick={() => togglePanel("tasks")}
                aria-label="Toggle tasks panel"
              >
                <ListChecks size={16} />
              </IconButton>
            )}
            <IconButton active={sidePanel === "files"} onClick={() => togglePanel("files")} aria-label="Toggle files panel">
              <FolderOpen size={16} />
            </IconButton>
            <IconButton
              active={sidePanel === "sandbox"}
              onClick={() => togglePanel("sandbox")}
              aria-label="Toggle sandbox panel"
            >
              <TerminalSquare size={16} />
            </IconButton>
            <IconButton
              active={sidePanel === "activity"}
              onClick={() => togglePanel("activity")}
              aria-label="Toggle activity log"
            >
              <ListTree size={16} />
            </IconButton>
          </div>
        </header>

        <ChatThread
          items={items}
          interruptPayload={project.interrupt?.payload}
          pendingSubmission={pendingSubmission}
          working={working}
          progress={progress}
          onRetry={retry}
          retryBusy={busy}
          onSendChat={(text) => sendChat(text)}
        />

        <ChatComposer
          activeGate={activeGate}
          payload={project.interrupt?.payload}
          busy={busy}
          working={working}
          error={submitError}
          onSubmit={submit}
          onSendChat={sendChat}
        />
      </div>

      {sidePanel === "tasks" && (
        <TasksPanel
          tickets={tickets}
          currentIndex={project.state.current_ticket_index ?? 0}
          outcomes={project.state.ticket_outcomes ?? {}}
          stage={project.stage}
          open
          onClose={() => setSidePanel(null)}
        />
      )}
      <FilesPanel projectId={projectId} open={sidePanel === "files"} onClose={() => setSidePanel(null)} />
      <SandboxPanel projectId={projectId} open={sidePanel === "sandbox"} onClose={() => setSidePanel(null)} />
      <ActivityDrawer
        events={events}
        connected={connected}
        open={sidePanel === "activity"}
        onClose={() => setSidePanel(null)}
      />
    </div>
  );
}
