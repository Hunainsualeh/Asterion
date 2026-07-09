"use client";

import { useCallback, useEffect, useState } from "react";
import { AlarmClock, Check, Flag, Pencil, Plus, Repeat, Tag, Trash2, X } from "lucide-react";
import { useAppUI } from "@/hooks/useAppUI";
import {
  completeTask,
  createTask,
  deleteTask,
  listCategories,
  listTasks,
  updateTask,
  type Task,
  type TaskCategory,
  type TaskInput,
  type TaskPriority,
  type TaskStatus,
} from "@/lib/api";

type FilterKey = "open" | "done" | "missed" | "all";
const FILTERS: { key: FilterKey; label: string; status?: string }[] = [
  { key: "open", label: "Active", status: "open,in_progress" },
  { key: "done", label: "Done", status: "done" },
  { key: "missed", label: "Missed", status: "missed" },
  { key: "all", label: "All" },
];

const PRIORITY_STYLE: Record<TaskPriority, string> = {
  urgent: "bg-danger-bg text-danger",
  high: "bg-warning-bg text-warning",
  normal: "bg-surface-2 text-text-tertiary",
  low: "bg-surface-2 text-text-tertiary",
};

const RECURRENCE_PRESETS: { label: string; value: string }[] = [
  { label: "Does not repeat", value: "" },
  { label: "Daily", value: "FREQ=DAILY" },
  { label: "Weekly", value: "FREQ=WEEKLY" },
  { label: "Monthly", value: "FREQ=MONTHLY" },
  { label: "Yearly", value: "FREQ=YEARLY" },
];

const REMINDER_PRESETS: { label: string; value: number }[] = [
  { label: "At time of task", value: 0 },
  { label: "10 minutes before", value: 10 },
  { label: "1 hour before", value: 60 },
  { label: "1 day before", value: 1440 },
];

const WEEKDAY_CODES = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"];

function fmtDue(t: Task): string {
  if (!t.due_at) return "";
  const d = new Date(t.due_at);
  const date = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  return t.due_has_time ? `${date}, ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}` : date;
}

function describeRecurrence(rrule: string | null): string {
  if (!rrule) return "";
  const freq = /FREQ=(\w+)/.exec(rrule)?.[1];
  const map: Record<string, string> = { DAILY: "daily", WEEKLY: "weekly", MONTHLY: "monthly", YEARLY: "yearly" };
  return freq ? map[freq] ?? "repeats" : "repeats";
}

export default function TaskManager() {
  const { tasksVersion } = useAppUI();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [categories, setCategories] = useState<TaskCategory[]>([]);
  const [filter, setFilter] = useState<FilterKey>("open");
  const [editing, setEditing] = useState<Task | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const status = FILTERS.find((f) => f.key === filter)?.status;
      const [{ tasks: t }, { categories: c }] = await Promise.all([
        listTasks(status ? { status } : {}),
        listCategories(),
      ]);
      setTasks(t);
      setCategories(c);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load tasks");
    }
  }, [filter]);

  useEffect(() => {
    load();
  }, [load, tasksVersion]);

  async function onComplete(t: Task) {
    setTasks((prev) => prev.map((x) => (x.id === t.id ? { ...x, status: "done" } : x)));
    try {
      await completeTask(t.id);
    } finally {
      load();
    }
  }
  async function onDelete(t: Task) {
    setTasks((prev) => prev.filter((x) => x.id !== t.id));
    try {
      await deleteTask(t.id);
    } finally {
      load();
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="mb-3 flex items-center gap-2">
        <div className="flex flex-1 gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                filter === f.key ? "bg-accent-soft text-accent" : "text-text-secondary hover:bg-surface-2"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <button
          onClick={() => setEditing("new")}
          className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground transition-colors hover:bg-accent-hover"
        >
          <Plus size={14} /> New task
        </button>
      </div>

      {error && <p className="mb-2 text-xs text-danger">{error}</p>}

      <div className="-mx-1 flex-1 overflow-y-auto scroll-thin px-1">
        {tasks.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-center text-text-tertiary">
            <AlarmClock size={22} />
            <p className="text-sm">No tasks here.</p>
            <p className="text-xs">Create one, or just say “remind me tomorrow at 9am to…” in chat.</p>
          </div>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {tasks.map((t) => (
              <li
                key={t.id}
                className="group flex items-start gap-3 rounded-xl border border-border-soft bg-surface px-3 py-2.5"
              >
                <button
                  onClick={() => onComplete(t)}
                  disabled={t.status === "done"}
                  aria-label="Mark done"
                  className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-colors ${
                    t.status === "done"
                      ? "border-success bg-success text-white"
                      : "border-border hover:border-accent"
                  }`}
                >
                  {t.status === "done" && <Check size={12} />}
                </button>
                <div className="min-w-0 flex-1">
                  <p
                    className={`truncate text-sm font-medium ${
                      t.status === "done" ? "text-text-tertiary line-through" : "text-text-primary"
                    }`}
                  >
                    {t.title}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
                    {t.due_at && (
                      <span
                        className={`inline-flex items-center gap-1 ${
                          t.status === "missed" ? "text-danger" : "text-text-secondary"
                        }`}
                      >
                        <AlarmClock size={11} /> {fmtDue(t)}
                      </span>
                    )}
                    {t.recurrence && (
                      <span className="inline-flex items-center gap-1 text-text-tertiary">
                        <Repeat size={11} /> {describeRecurrence(t.recurrence)}
                      </span>
                    )}
                    {t.priority !== "normal" && (
                      <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 ${PRIORITY_STYLE[t.priority]}`}>
                        <Flag size={10} /> {t.priority}
                      </span>
                    )}
                    {t.tags.map((tag) => (
                      <span key={tag} className="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-text-tertiary">
                        <Tag size={10} /> {tag}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                  <button
                    onClick={() => setEditing(t)}
                    aria-label="Edit task"
                    className="flex h-7 w-7 items-center justify-center rounded-lg text-text-tertiary hover:bg-surface-2 hover:text-text-primary"
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    onClick={() => onDelete(t)}
                    aria-label="Delete task"
                    className="flex h-7 w-7 items-center justify-center rounded-lg text-text-tertiary hover:bg-danger-bg hover:text-danger"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {editing !== null && (
        <TaskForm
          task={editing === "new" ? null : editing}
          categories={categories}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create / edit form (modal)
// ---------------------------------------------------------------------------
function TaskForm({
  task,
  categories,
  onClose,
  onSaved,
}: {
  task: Task | null;
  categories: TaskCategory[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const initialDate = task?.due_at ? new Date(task.due_at) : null;
  const [title, setTitle] = useState(task?.title ?? "");
  const [description, setDescription] = useState(task?.description ?? "");
  const [date, setDate] = useState(initialDate ? toDateInput(initialDate) : "");
  const [time, setTime] = useState(initialDate && task?.due_has_time ? toTimeInput(initialDate) : "");
  const [priority, setPriority] = useState<TaskPriority>(task?.priority ?? "normal");
  const [recurrence, setRecurrence] = useState(baseFreq(task?.recurrence ?? ""));
  const [reminderMin, setReminderMin] = useState<number>(task?.reminders?.[0]?.offset_min ?? 0);
  const [tags, setTags] = useState((task?.tags ?? []).join(", "));
  const [categoryId, setCategoryId] = useState(task?.category_id ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const canSave = title.trim().length > 0;

  async function save() {
    if (!canSave || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const due = date ? (time ? `${date}T${time}` : date) : null;
      const recur = buildRecurrence(recurrence, date);
      const body: TaskInput & { status?: TaskStatus } = {
        title: title.trim(),
        description: description.trim(),
        due,
        due_has_time: due ? Boolean(time) : null,
        priority,
        recurrence: recur || null,
        reminders: due ? [{ offset_min: reminderMin }] : [],
        tags: tags.split(",").map((s) => s.trim()).filter(Boolean),
        category_id: categoryId || null,
      };
      if (task) await updateTask(task.id, body);
      else await createTask(body);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't save the task");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4" onMouseDown={onClose}>
      <div
        className="w-full max-w-md rounded-2xl border border-border bg-surface-raised p-5 shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-text-primary">{task ? "Edit task" : "New task"}</h3>
          <button onClick={onClose} aria-label="Close" className="text-text-tertiary hover:text-text-primary">
            <X size={16} />
          </button>
        </div>

        <div className="flex flex-col gap-3">
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="What needs doing?"
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary focus:border-accent focus:outline-none"
          />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Notes (optional)"
            rows={2}
            className="w-full resize-none rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary focus:border-accent focus:outline-none"
          />
          <div className="flex gap-2">
            <Field label="Date">
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={inputCls} />
            </Field>
            <Field label="Time">
              <input type="time" value={time} onChange={(e) => setTime(e.target.value)} disabled={!date} className={inputCls} />
            </Field>
          </div>
          <div className="flex gap-2">
            <Field label="Priority">
              <select value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)} className={inputCls}>
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </Field>
            <Field label="Repeat">
              <select value={recurrence} onChange={(e) => setRecurrence(e.target.value)} className={inputCls}>
                {RECURRENCE_PRESETS.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="Reminder">
            <select
              value={reminderMin}
              onChange={(e) => setReminderMin(Number(e.target.value))}
              disabled={!date}
              className={inputCls}
            >
              {REMINDER_PRESETS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </Field>
          <div className="flex gap-2">
            <Field label="Tags">
              <input
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="comma, separated"
                className={inputCls}
              />
            </Field>
            {categories.length > 0 && (
              <Field label="Category">
                <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)} className={inputCls}>
                  <option value="">None</option>
                  {categories.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </Field>
            )}
          </div>
          {err && <p className="text-xs text-danger">{err}</p>}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3 py-2 text-sm text-text-secondary hover:bg-surface-2">
            Cancel
          </button>
          <button
            onClick={save}
            disabled={!canSave || busy}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition-colors hover:bg-accent-hover disabled:opacity-50"
          >
            {busy ? "Saving…" : task ? "Save changes" : "Create task"}
          </button>
        </div>
      </div>
    </div>
  );
}

const inputCls =
  "w-full rounded-lg border border-border bg-surface px-2.5 py-2 text-sm text-text-primary focus:border-accent focus:outline-none";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-1 flex-col gap-1">
      <span className="text-[11px] font-medium uppercase tracking-wide text-text-tertiary">{label}</span>
      {children}
    </label>
  );
}

function toDateInput(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function toTimeInput(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
function baseFreq(rrule: string): string {
  const freq = /FREQ=(\w+)/.exec(rrule)?.[1];
  return freq ? `FREQ=${freq}` : "";
}
/** Weekly repeats bind to the chosen date's weekday so "weekly" means "every
 * <that day>". Other frequencies need no extra parts for this UI. */
function buildRecurrence(preset: string, date: string): string {
  if (!preset) return "";
  if (preset === "FREQ=WEEKLY" && date) {
    const day = WEEKDAY_CODES[new Date(`${date}T00:00`).getDay()];
    return `FREQ=WEEKLY;BYDAY=${day}`;
  }
  return preset;
}
