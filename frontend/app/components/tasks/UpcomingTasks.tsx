"use client";

import { useEffect, useState } from "react";
import { AlarmClock, ChevronRight } from "lucide-react";
import { useAppUI } from "@/hooks/useAppUI";
import { tasksSummary, type Task } from "@/lib/api";

function fmt(t: Task): string {
  if (!t.due_at) return "";
  const d = new Date(t.due_at);
  const day = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  return t.due_has_time ? `${day} · ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}` : day;
}

/** A quiet summary of what's coming up, shown on the home screen. Only renders
 * when there's something due — never adds empty chrome. */
export default function UpcomingTasks() {
  const { openSettings, tasksVersion } = useAppUI();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [overdue, setOverdue] = useState(0);

  useEffect(() => {
    tasksSummary()
      .then((s) => {
        setTasks(s.upcoming.slice(0, 3));
        setOverdue(s.overdue.length);
      })
      .catch(() => {});
  }, [tasksVersion]);

  if (tasks.length === 0 && overdue === 0) return null;

  return (
    <div className="mx-auto mt-8 w-full max-w-md">
      <button
        onClick={() => openSettings("tasks")}
        className="flex w-full items-center gap-2 rounded-xl border border-border bg-surface px-3 py-2 text-left transition-colors hover:border-accent/40"
      >
        <AlarmClock size={14} className="shrink-0 text-accent" />
        <span className="text-xs font-medium uppercase tracking-wider text-text-tertiary">Upcoming</span>
        {overdue > 0 && (
          <span className="rounded-full bg-danger-bg px-1.5 py-0.5 text-[10px] font-semibold text-danger">
            {overdue} overdue
          </span>
        )}
        <span className="flex-1" />
        <ChevronRight size={14} className="text-text-tertiary" />
      </button>
      {tasks.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1">
          {tasks.map((t) => (
            <li key={t.id} className="flex items-center gap-2 px-3 text-xs text-text-secondary">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent/60" />
              <span className="min-w-0 flex-1 truncate">{t.title}</span>
              <span className="shrink-0 text-text-tertiary">{fmt(t)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
