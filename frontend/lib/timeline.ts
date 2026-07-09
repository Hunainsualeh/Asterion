import type { DagNode, DagSnapshot, GateKind, PipelineEvent, ProjectDetail, QARound, Ticket, Tone } from "./api";

export interface NarrationItem {
  type: "narration";
  key: string;
  agent: string;
  headline: string;
  detail?: string;
  tone: Tone;
  ts: number;
}

export interface GateItem {
  type: "gate";
  key: string;
  agent: string;
  gate: string;
  gateKind: GateKind;
  headline: string;
  detail?: string;
  /** True only for the single gate currently awaiting a human response. */
  live: boolean;
  ts: number;
}

export interface DocumentItem {
  type: "document";
  key: string;
  title: string;
  doc: string;
  qa?: QARound[];
  ts: number;
}

export interface TicketsItem {
  type: "tickets";
  key: string;
  tickets: Ticket[];
  ts: number;
}

export interface ErrorItem {
  type: "error";
  key: string;
  title: string;
  explanation: string;
  suggestion: string;
  retryable: boolean;
  reference: string;
  ts: number;
}

export interface ResultItem {
  type: "result";
  key: string;
  agent: string;
  markdown: string;
  partial: boolean;
  /** True for task-lane answers: render as a plain assistant message, not a
   * ceremonial "Final result" card. */
  plain: boolean;
  ts: number;
}

export interface UserItem {
  type: "user";
  key: string;
  text: string;
  ts: number;
}

export interface ClarifyItem {
  type: "clarify";
  key: string;
  agent: string;
  intro: string;
  questions: string[];
  /** True only while this is the newest clarify still awaiting an answer. */
  live: boolean;
  ts: number;
}

export interface DagItem {
  type: "dag";
  key: string;
  dag: DagSnapshot;
  ts: number;
}

export type TimelineItem =
  | NarrationItem
  | GateItem
  | DocumentItem
  | TicketsItem
  | ErrorItem
  | ResultItem
  | DagItem
  | UserItem
  | ClarifyItem;

function gateKindOf(gate: string): GateKind {
  if (gate === "MANUAL_TEST") return "manual_test";
  if (gate.endsWith("_CLARIFY")) return "clarify";
  return "approval";
}

/** Backend/frontend version skew (e.g. a server process older than the
 * `friendly` schema) must degrade to a plainer feed, never a blank one —
 * gates and errors always render, other events fall back to technical-only. */
function fallbackFriendly(ev: PipelineEvent): NonNullable<PipelineEvent["friendly"]> {
  if (ev.kind === "gate") {
    const qs = (ev.data?.questions ?? []) as string[];
    return {
      headline: ev.message || "I need your input to continue.",
      detail: qs.length ? qs.map((q) => `• ${q}`).join("\n") : null,
      tone: "waiting",
      chat: true,
    };
  }
  if (ev.kind === "error") {
    return { headline: ev.message || "Something went wrong", detail: null, tone: "error", chat: true };
  }
  return { headline: ev.message || ev.kind.replace(/_/g, " "), detail: null, tone: "info", chat: false };
}

const DAG_EVENT_KINDS = new Set([
  "dag_started",
  "dag_finished",
  "node_started",
  "node_finished",
  "node_failed",
  "node_retry",
  "node_skipped",
]);

/** Turns the raw event log + current project state into an ordered feed a
 * non-technical user can read top to bottom like a conversation. Events
 * marked `friendly.chat === false` are technical-only — they still show up in
 * the Activity drawer via the raw event list, just not here. DAG progress
 * events collapse into a single live card that upgrades in place. */
export function buildTimeline(
  events: PipelineEvent[],
  project: ProjectDetail | null,
  submittingInterruptId: string | null = null,
): TimelineItem[] {
  if (!project) return [];
  const items: TimelineItem[] = [];
  const state = project.state ?? {};
  const interrupt = project.interrupt;
  const lastGateIndex = findLastIndex(events, (e) => e.kind === "gate");
  const lastClarifyIndex = findLastIndex(
    events,
    (e) => e.kind === "result" && Array.isArray(e.data?.questions) && (e.data.questions as unknown[]).length > 0,
  );
  // The clarify picker stays interactive only until it's answered — i.e. until a
  // user reply or a real (non-question) result appears after it.
  const clarifyAnswered =
    lastClarifyIndex >= 0 &&
    events.slice(lastClarifyIndex + 1).some(
      (e) =>
        e.kind === "user_message" ||
        (e.kind === "result" && !(Array.isArray(e.data?.questions) && (e.data.questions as unknown[]).length > 0)),
    );
  let lastGateSig: string | null = null;
  let dagItem: DagItem | null = null;
  // Per-node full details (output/error) accumulated as each step finishes.
  // The compact whole-DAG payload on every frame omits `output` to stay small,
  // but each node_* event *also* carries that node's full snapshot — capture it
  // here so an expanded step shows what the agent actually produced.
  const dagNodeDetails = new Map<string, Partial<DagNode>>();
  const plainResults = project.lane === "task";

  // The opening message: the idea IS the user's first chat bubble. Follow-ups
  // arrive as user_message events; the first turn predates that mechanism.
  if (project.idea) {
    items.push({ type: "user", key: "user-idea", text: project.idea, ts: 0 });
  }

  events.forEach((ev, i) => {
    const friendly = ev.friendly ?? fallbackFriendly(ev);
    const key = ev.id ?? `${ev.kind}-${i}-${ev.ts}`;

    if (ev.kind === "user_message") {
      const text = ev.message || String(ev.data?.text ?? "");
      if (text) items.push({ type: "user", key, text, ts: ev.ts });
      return;
    }

    if (DAG_EVENT_KINDS.has(ev.kind)) {
      // Capture this event's own node snapshot — it carries `output`/`error`
      // that the compact whole-DAG payload drops. Guard against nulls so a
      // later node_started (output still null) can't wipe a finished node's
      // captured output.
      const node = ev.data?.node as DagNode | undefined;
      if (node?.id) {
        const prev = dagNodeDetails.get(node.id) ?? {};
        dagNodeDetails.set(node.id, {
          ...prev,
          ...(node.output != null ? { output: node.output } : {}),
          ...(node.error ? { error: node.error } : {}),
        });
      }

      // One card for the whole run, updated in place from the latest event's
      // embedded whole-DAG payload (every frame carries full node statuses).
      const dag = ev.data?.dag as DagSnapshot | undefined;
      if (!dag?.nodes) return;
      // Splice captured per-node output/error back into the compact nodes.
      const enrich = (n: DagNode): DagNode => {
        const d = dagNodeDetails.get(n.id);
        return d ? { ...n, ...d } : n;
      };
      const enrichedDag: DagSnapshot = { ...dag, nodes: dag.nodes.map(enrich) };
      if (dagItem && dagItem.dag.run_id === enrichedDag.run_id) {
        dagItem.dag = { ...dagItem.dag, ...enrichedDag };
        return;
      }
      // A one-step "plan" is noise (a greeting answered directly still runs
      // as a 1-node DAG) — only show the card once there's real structure.
      // Planner runs start at 1 node and expand; the card appears on expansion.
      if (enrichedDag.nodes.length <= 1) return;
      dagItem = { type: "dag", key: `dag-${enrichedDag.run_id}`, dag: enrichedDag, ts: ev.ts };
      items.push(dagItem);
      return;
    }

    if (ev.kind === "result") {
      // A clarification ask carries a structured `questions` list — render it
      // as an interactive picker instead of a plain markdown message.
      const questions = (ev.data?.questions ?? []) as string[];
      if (Array.isArray(questions) && questions.length) {
        items.push({
          type: "clarify",
          key,
          agent: ev.agent || "system",
          intro: String(ev.data?.intro ?? ""),
          questions,
          live: i === lastClarifyIndex && !project.running && !clarifyAnswered,
          ts: ev.ts,
        });
        return;
      }
      const markdown = String(ev.data?.result ?? "");
      if (markdown) {
        items.push({
          type: "result",
          key,
          agent: ev.agent || "system",
          markdown,
          partial: Boolean(ev.data?.partial),
          plain: plainResults,
          ts: ev.ts,
        });
      }
      return;
    }

    if (ev.kind === "gate") {
      const gate = String(ev.data?.gate ?? "");
      // LangGraph re-runs a node's pre-interrupt code on every resume, so the
      // same gate can get published twice back to back (once on first pause,
      // once again the instant it's approved and the node replays). Collapse
      // an exact repeat of the immediately preceding gate into one card.
      const sig = `${gate}|${friendly.headline}|${friendly.detail ?? ""}`;
      if (sig === lastGateSig) return;
      lastGateSig = sig;

      // Live = this is the newest gate event, a pause is actually pending for
      // this gate, and the user hasn't already answered THIS pause (compared
      // by interrupt id, not gate name — the same gate can fire again later
      // and must re-enable).
      const live = Boolean(
        interrupt &&
          i === lastGateIndex &&
          interrupt.gate === gate &&
          (interrupt.interrupt_id ?? "") !== (submittingInterruptId ?? "__none__"),
      );
      items.push({
        type: "gate",
        key,
        agent: ev.agent,
        gate,
        gateKind: gateKindOf(gate),
        headline: friendly.headline,
        detail: friendly.detail ?? undefined,
        live,
        ts: ev.ts,
      });

      if (gate === "APPROVE_SCOPE" && state.scope_doc) {
        items.push({
          type: "document",
          key: `${key}-doc`,
          title: "Scope",
          doc: state.scope_doc,
          qa: state.scope_qa,
          ts: ev.ts + 0.001,
        });
      } else if (gate === "APPROVE_ARCHITECTURE" && state.architecture_doc) {
        items.push({
          type: "document",
          key: `${key}-doc`,
          title: "Architecture",
          doc: state.architecture_doc,
          qa: state.architecture_qa,
          ts: ev.ts + 0.001,
        });
      } else if (gate === "APPROVE_TICKETS" && state.tickets?.length) {
        items.push({ type: "tickets", key: `${key}-tix`, tickets: state.tickets, ts: ev.ts + 0.001 });
      }
      return;
    }

    if (ev.kind === "error") {
      const fe = (ev.data?.friendly_error ?? {}) as Partial<{
        title: string;
        explanation: string;
        suggestion: string;
        retryable: boolean;
        reference: string;
      }>;
      items.push({
        type: "error",
        key,
        title: fe.title ?? friendly.headline,
        explanation: fe.explanation ?? friendly.detail ?? "",
        suggestion: fe.suggestion ?? "",
        retryable: fe.retryable ?? true,
        reference: fe.reference ?? "",
        ts: ev.ts,
      });
      return;
    }

    if (!friendly.chat) return;

    items.push({
      type: "narration",
      key,
      agent: ev.agent,
      headline: friendly.headline,
      detail: friendly.detail ?? undefined,
      tone: friendly.tone,
      ts: ev.ts,
    });
  });

  // A completed project whose event history got trimmed (or a fresh page
  // load without replay) must still show the final deliverable: fall back to
  // the persisted result when no result event made it into the feed.
  if (!items.some((it) => it.type === "result") && project.result?.result) {
    items.push({
      type: "result",
      key: "result-persisted",
      agent: "system",
      markdown: project.result.result,
      partial: project.result.status !== "succeeded",
      plain: plainResults,
      ts: Number.MAX_SAFE_INTEGER,
    });
  }

  return items;
}

function findLastIndex<T>(arr: T[], pred: (item: T) => boolean): number {
  for (let i = arr.length - 1; i >= 0; i--) {
    if (pred(arr[i])) return i;
  }
  return -1;
}

/** True while the pipeline is actively working with nothing pending on the
 * human — used to drive the "thinking" indicator at the bottom of the chat. */
export function isWorking(project: ProjectDetail | null, submitting: boolean): boolean {
  if (!project) return false;
  if (submitting) return true;
  return project.running && !project.interrupt;
}

export interface LiveProgress {
  headline: string;
  /** The agent currently doing the work — drives the live avatar/name. */
  agent: string;
}

export function latestProgress(project: ProjectDetail | null): LiveProgress {
  const stage = project?.stage;
  if (stage?.friendly?.tone === "progress" || stage?.friendly?.tone === "info") {
    return { headline: stage.friendly.headline, agent: stage.agent || "system" };
  }
  return { headline: "Working on it...", agent: "system" };
}
