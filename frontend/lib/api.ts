const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

/** The viewer's IANA timezone, so the backend Task Agent can resolve
 * "tomorrow at 9am" to the right absolute instant. */
export function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export interface Ticket {
  id: string;
  title: string;
  description: string;
  acceptance_criteria: string[];
  test_checklist: string[];
  dependencies: string[];
  effort: string;
  status: string;
}

export interface QARound {
  questions: string[];
  answer: string;
}

export type GateKind = "approval" | "clarify" | "manual_test";
export type Tone = "info" | "progress" | "waiting" | "success" | "error";

export interface InterruptPayload {
  kind: GateKind;
  gate: string;
  summary: string;
  payload: Record<string, unknown>;
  /** Unique per pause — a re-fired gate gets a new id (drives composer re-enable). */
  interrupt_id?: string;
  ts?: number;
}

export type NodeStatus =
  | "pending"
  | "ready"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped"
  | "cancelled";

export interface DagNode {
  id: string;
  name: string;
  agent: string;
  deps: string[];
  status: NodeStatus;
  attempts: number;
  duration_ms: number | null;
  output?: string | null;
  error?: string;
}

export interface DagSnapshot {
  run_id: string;
  status: string;
  label?: string;
  query?: string;
  started_at?: number | null;
  finished_at?: number | null;
  duration_ms?: number | null;
  error?: string;
  nodes: DagNode[];
  edges?: { from: string; to: string }[];
}

export interface ProjectResult {
  run_id?: string;
  status: string;
  result: string;
  label?: string;
}

export interface IntentInfo {
  kind?: string;
  complexity?: string;
  confidence?: number;
  reason?: string;
  lane?: string;
  source?: string;
}

export interface ArtifactEntry {
  path: string;
  size: number;
  modified: number;
}

export interface SandboxSession {
  id: string;
  command: string;
  background: boolean;
  status: "running" | "exited" | "killed" | "timeout" | "error";
  returncode: number | null;
  started_at: number;
  finished_at: number | null;
  lines: number;
  /** Dev-server URL detected from output (e.g. http://localhost:3000), if any. */
  url?: string | null;
}

export interface SandboxLogEntry {
  session: string;
  stream: "stdout" | "stderr" | "system";
  line: string;
  ts: number;
}

export interface TicketOutcome {
  title: string;
  summary: string;
  review_notes: string;
  auto_test_summary: string;
  risk: Record<string, unknown>;
  files_changed: string[];
}

export interface FriendlyEvent {
  headline: string;
  detail: string | null;
  tone: Tone;
  /** false = technical-only; don't render as its own chat bubble. */
  chat: boolean;
}

export interface FriendlyErrorPayload {
  title: string;
  explanation: string;
  suggestion: string;
  retryable: boolean;
  reference: string;
}

export interface StageSnapshot {
  kind: string;
  agent: string;
  friendly: FriendlyEvent;
}

export interface ProjectSummary {
  project_id: string;
  idea: string;
  title: string;
  summary: string;
  status: string;
  lane: "project" | "task";
  intent: IntentInfo;
  pending_gate: string | null;
  running: boolean;
  stage: StageSnapshot | null;
}

export interface RiskAssessment {
  tier: 0 | 1 | 2;
  score: number;
  category: string;
  reasons: string[];
}

export interface SecurityFinding {
  kind: string;
  file: string;
  line: number;
  match: string;
  severity: string;
}

export interface ProjectState {
  status?: string;
  current_agent?: string;
  scope_doc?: string;
  scope_qa?: QARound[];
  architecture_doc?: string;
  architecture_qa?: QARound[];
  tickets?: Ticket[];
  current_ticket_index?: number;
  ticket_outcomes?: Record<string, TicketOutcome>;
  review_result?: string;
  review_notes?: string;
  dev_notes?: string;
  debug_notes?: string;
  test_result?: string;
  test_feedback?: string;
  branch?: string;
  risk?: RiskAssessment;
  auto_test_summary?: string;
  security_findings?: SecurityFinding[];
}

export interface ProjectDetail extends ProjectSummary {
  interrupt: InterruptPayload | null;
  state: ProjectState;
  result: ProjectResult | null;
  dag: DagSnapshot | null;
}

export interface PipelineEvent {
  /** Redis stream entry id — globally unique, used for client-side dedupe. */
  id?: string;
  kind: string;
  agent: string;
  message: string;
  data: Record<string, unknown>;
  friendly?: FriendlyEvent;
  ts: number;
}

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new ApiError(text || res.statusText, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export type AgentMode = "auto" | "research";

export interface UploadedFile {
  name: string;
  kind: string;
  chars: number;
}

/** Upload attachments to a staging batch; returns the batch id + extracted-file metadata. */
export const uploadAttachments = async (files: FileList | File[]): Promise<{ batch_id: string; files: UploadedFile[] }> => {
  const form = new FormData();
  Array.from(files).forEach((f) => form.append("files", f));
  const res = await fetch(`${API_BASE}/uploads`, { method: "POST", body: form });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new ApiError(text || res.statusText, res.status);
  }
  return res.json();
};

export interface RunOptions {
  lane?: "auto" | "project" | "task";
  mode?: AgentMode;
  attachmentBatchId?: string;
  tone?: string;
}

export const startProject = (idea: string, opts: RunOptions = {}) =>
  api<{ project_id: string; status: string; lane: string; intent: IntentInfo }>("/projects", {
    method: "POST",
    body: JSON.stringify({
      idea,
      lane: opts.lane ?? "auto",
      mode: opts.mode ?? "auto",
      attachment_batch_id: opts.attachmentBatchId ?? "",
      tone: opts.tone ?? "",
      timezone: browserTz(),
    }),
  });

export const listProjects = () => api<ProjectSummary[]>("/projects");

export const getProject = (pid: string) => api<ProjectDetail>(`/projects/${pid}`);

export const approveGate = (pid: string, action: "approve" | "reject", feedback = "", interruptId = "") =>
  api<{ ok: boolean }>(`/projects/${pid}/approve`, {
    method: "POST",
    body: JSON.stringify({ action, feedback, interrupt_id: interruptId }),
  });

export const submitManualTest = (pid: string, result: "pass" | "fail", feedback = "", interruptId = "") =>
  api<{ ok: boolean }>(`/projects/${pid}/test`, {
    method: "POST",
    body: JSON.stringify({ result, feedback, interrupt_id: interruptId }),
  });

export const answerClarification = (pid: string, feedback: string, interruptId = "") =>
  api<{ ok: boolean }>(`/projects/${pid}/answer`, {
    method: "POST",
    body: JSON.stringify({ feedback, interrupt_id: interruptId }),
  });

export const sendMessage = (
  pid: string,
  message: string,
  opts: { mode?: AgentMode; attachmentBatchId?: string; tone?: string } = {},
) =>
  api<{ ok: boolean; intent: IntentInfo }>(`/projects/${pid}/message`, {
    method: "POST",
    body: JSON.stringify({
      message,
      mode: opts.mode ?? "auto",
      attachment_batch_id: opts.attachmentBatchId ?? "",
      tone: opts.tone ?? "",
      timezone: browserTz(),
    }),
  });

export const retryProject = (pid: string) =>
  api<{ ok: boolean }>(`/projects/${pid}/retry`, { method: "POST" });

export const cancelProject = (pid: string) =>
  api<{ ok: boolean }>(`/projects/${pid}/cancel`, { method: "POST" });

export const deleteProject = (pid: string) =>
  api<{ ok: boolean }>(`/projects/${pid}`, { method: "DELETE" });

export const deleteAllProjects = () =>
  api<{ ok: boolean; deleted: number }>(`/projects`, { method: "DELETE" });

export const renameProject = (pid: string, title: string) =>
  api<{ ok: boolean; title: string }>(`/projects/${pid}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });

export const getDag = (pid: string) =>
  api<{ live: DagSnapshot | null; runs: DagSnapshot[] }>(`/projects/${pid}/dag`);

export const getResult = (pid: string) => api<{ result: ProjectResult | null }>(`/projects/${pid}/result`);

export const listArtifacts = (pid: string) =>
  api<{ docs: ArtifactEntry[]; repo: ArtifactEntry[] }>(`/projects/${pid}/artifacts`);

export const readArtifact = (pid: string, root: "repo" | "docs", path: string) =>
  api<{ path: string; root: string; content: string; truncated: boolean; binary: boolean; size: number }>(
    `/projects/${pid}/artifacts/content?root=${root}&path=${encodeURIComponent(path)}`,
  );

export const writeArtifact = (pid: string, path: string, content: string) =>
  api<{ saved: string; bytes: number }>(`/projects/${pid}/artifacts/content?root=repo`, {
    method: "PUT",
    body: JSON.stringify({ path, content }),
  });

export const sandboxRun = (pid: string, command: string, timeoutS = 120, background = false) =>
  api<{ session: SandboxSession }>(`/projects/${pid}/sandbox/run`, {
    method: "POST",
    body: JSON.stringify({ command, timeout_s: timeoutS, background }),
  });

export const sandboxSessions = (pid: string) =>
  api<{ sessions: SandboxSession[] }>(`/projects/${pid}/sandbox/sessions`);

export const sandboxKill = (pid: string, sid: string) =>
  api<{ ok: boolean }>(`/projects/${pid}/sandbox/kill/${sid}`, { method: "POST" });

export const getProjectMetrics = (pid: string) => api<Record<string, unknown>>(`/projects/${pid}/metrics`);

export function eventsUrl(pid: string): string {
  return `${API_BASE}/projects/${pid}/events`;
}

export function sandboxStreamUrl(pid: string): string {
  return `${API_BASE}/projects/${pid}/sandbox/stream`;
}

export function rawArtifactUrl(pid: string, root: "repo" | "docs", path: string): string {
  return `${API_BASE}/projects/${pid}/artifacts/raw/${path
    .split("/")
    .map(encodeURIComponent)
    .join("/")}?root=${root}`;
}

// ===========================================================================
// Assistant platform: tasks, notifications, system control
// ===========================================================================
export type TaskStatus = "open" | "in_progress" | "done" | "missed" | "cancelled";
export type TaskPriority = "low" | "normal" | "high" | "urgent";

export interface TaskReminder {
  id?: string;
  offset_min: number;
  channel: string;
  fire_at?: string;
  fired_at?: string | null;
}

export interface Task {
  id: string;
  user_id: string;
  title: string;
  description: string;
  status: TaskStatus;
  priority: TaskPriority;
  due_at: string | null;
  due_has_time: boolean;
  timezone: string;
  recurrence: string | null;
  category_id: string | null;
  chat_id: string | null;
  source: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  tags: string[];
  reminders: TaskReminder[];
  events?: { ts: string; kind: string; actor: string; detail: unknown }[];
}

export interface TaskCategory {
  id: string;
  name: string;
  color: string;
}

export interface TaskSummary {
  counts: Record<TaskStatus, number>;
  upcoming: Task[];
  overdue: Task[];
}

export interface TaskInput {
  title: string;
  description?: string;
  due?: string | null;
  due_has_time?: boolean | null;
  timezone?: string;
  priority?: TaskPriority;
  recurrence?: string | null;
  reminders?: { offset_min: number; channel?: string }[];
  tags?: string[];
  category_id?: string | null;
}

export const listTasks = (params: Record<string, string> = {}) => {
  const q = new URLSearchParams(params).toString();
  return api<{ tasks: Task[] }>(`/tasks${q ? `?${q}` : ""}`);
};
export const getTask = (id: string) => api<Task>(`/tasks/${id}`);
export const createTask = (body: TaskInput) =>
  api<Task>("/tasks", { method: "POST", body: JSON.stringify({ timezone: browserTz(), ...body }) });
export const updateTask = (id: string, body: Partial<TaskInput> & { status?: TaskStatus }) =>
  api<Task>(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const completeTask = (id: string) => api<Task>(`/tasks/${id}/complete`, { method: "POST" });
export const cancelTask = (id: string) => api<Task>(`/tasks/${id}/cancel`, { method: "POST" });
export const deleteTask = (id: string) => api<{ ok: boolean }>(`/tasks/${id}`, { method: "DELETE" });
export const tasksSummary = () => api<TaskSummary>("/tasks/summary");
export const listCategories = () => api<{ categories: TaskCategory[] }>("/categories");
export const createCategory = (name: string, color: string) =>
  api<TaskCategory>("/categories", { method: "POST", body: JSON.stringify({ name, color }) });
export const deleteCategory = (id: string) => api<{ ok: boolean }>(`/categories/${id}`, { method: "DELETE" });

export interface AppNotification {
  id: string;
  nid: string;
  kind: "reminder" | "missed" | "task" | "system";
  title: string;
  body: string;
  task_id: string;
  action: Record<string, unknown>;
  tone: "info" | "success" | "warning" | "error";
  ts: number;
  read?: boolean;
}

export const listNotifications = (limit = 50) =>
  api<{ notifications: AppNotification[]; unread: number }>(`/notifications?limit=${limit}`);
export const markNotificationRead = (id: string) =>
  api<{ ok: boolean; unread: number }>("/notifications/read", {
    method: "POST",
    body: JSON.stringify({ id }),
  });
export function notificationsUrl(): string {
  return `${API_BASE}/notifications/events`;
}

export interface ResolvedAction {
  action: string;
  target: string;
  params: Record<string, unknown>;
  destructive: boolean;
  confirm: boolean;
  label: string;
  audit_id: string;
}

export const auditAction = (auditId: string, phase: "executed" | "confirmed" | "cancelled" | "denied", action: string) =>
  api<{ ok: boolean }>("/control/audit", {
    method: "POST",
    body: JSON.stringify({ audit_id: auditId, phase, action }),
  });

// --------------------------------------------------------------------------- models
export type ProviderId = "groq" | "deepseek";

export interface LLMProvider {
  id: ProviderId;
  label: string;
  /** An API key is present. Says nothing about whether the key still works —
   * a DeepSeek key on a zero-balance account is `configured` but every
   * completion 402s. When that happens, `note` explains it. */
  configured: boolean;
  keys: number;
  note: string | null;
}

export interface LLMModel {
  /** Qualified id: DeepSeek models are namespaced (`deepseek/deepseek-v4-pro`). */
  id: string;
  provider: ProviderId;
  label: string;
  description: string;
  tier: string;
  reasoning: boolean;
  supports_tools: boolean;
  available: boolean;
}

export interface ModelCatalog {
  providers: LLMProvider[];
  models: LLMModel[];
  /** `null` = no override; each agent uses its model from litellm_config.yaml. */
  selected: string | null;
}

export const listModels = () => api<ModelCatalog>("/models");

/** Pass `null` to clear the override and restore per-agent routing. */
export const setModelSelection = (model: string | null) =>
  api<{ ok: boolean; selected: string | null }>("/models/selection", {
    method: "PUT",
    body: JSON.stringify({ model }),
  });

export { ApiError };
