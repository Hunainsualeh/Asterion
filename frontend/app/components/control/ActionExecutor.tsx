"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAppUI, type SettingsTab } from "@/hooks/useAppUI";
import { useTheme } from "@/hooks/useTheme";
import { auditAction, deleteAllProjects, deleteProject, type PipelineEvent, type ResolvedAction } from "@/lib/api";
import ConfirmDialog from "./ConfirmDialog";

/** Executes `ui_action` events emitted by the conversational system-control
 * layer. Non-destructive actions run immediately; destructive ones (delete
 * chat) prompt a confirmation first. Every outcome is reported back to the
 * audit trail. Mounted once inside the pipeline view — the active chat is where
 * control commands are issued. */
export default function ActionExecutor({ events, projectId }: { events: PipelineEvent[]; projectId: string }) {
  const router = useRouter();
  const { theme, toggle } = useTheme();
  const ui = useAppUI();
  const handled = useRef<Set<string>>(new Set());
  const [pending, setPending] = useState<ResolvedAction | null>(null);

  useEffect(() => {
    for (const ev of events) {
      if (ev.kind !== "ui_action") continue;
      const id = ev.id ?? `${ev.ts}`;
      if (handled.current.has(id)) continue;
      handled.current.add(id);

      const data = ev.data as unknown as ResolvedAction & { action: string };
      if (!data?.action) continue;
      if (data.action === "refresh_tasks") {
        ui.bumpTasks();
        continue;
      }
      if (data.confirm && data.destructive) {
        setPending(data);
      } else {
        execute(data);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  function execute(a: ResolvedAction) {
    switch (a.action) {
      case "open_settings":
        ui.openSettings((a.params?.tab as SettingsTab) ?? "general");
        break;
      case "open_tasks":
        ui.openSettings("tasks");
        break;
      case "open_profile":
        ui.openSettings("profile");
        break;
      case "show_notifications":
        ui.openNotifications();
        break;
      case "create_chat":
        router.push("/");
        break;
      case "toggle_sidebar":
        ui.toggleSidebar();
        break;
      case "switch_theme": {
        const want = a.params?.theme as string | undefined;
        if (!want || want === "toggle" || want !== theme) toggle();
        break;
      }
      case "delete_chat":
        deleteProject(a.target || projectId)
          .catch(() => {})
          .finally(() => {
            ui.bumpProjects();
            router.push("/");
          });
        break;
      case "delete_all_chats":
        deleteAllProjects()
          .catch(() => {})
          .finally(() => {
            ui.bumpProjects();
            router.push("/");
          });
        break;
      default:
        break;
    }
    if (a.audit_id) auditAction(a.audit_id, "executed", a.action).catch(() => {});
  }

  return (
    <ConfirmDialog
      open={pending !== null}
      title={pending?.label ? `${pending.label}?` : "Are you sure?"}
      body="This can't be undone."
      confirmLabel="Delete"
      destructive
      onConfirm={() => {
        if (pending) {
          if (pending.audit_id) auditAction(pending.audit_id, "confirmed", pending.action).catch(() => {});
          execute({ ...pending, confirm: false });
        }
        setPending(null);
      }}
      onCancel={() => {
        if (pending?.audit_id) auditAction(pending.audit_id, "cancelled", pending.action).catch(() => {});
        setPending(null);
      }}
    />
  );
}
