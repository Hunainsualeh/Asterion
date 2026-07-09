"use client";

import { useState } from "react";
import { Check, CircleDashed, Loader2, RotateCcw, SkipForward, X } from "lucide-react";
import Markdown from "@/app/components/ui/Markdown";
import Avatar from "@/app/components/ui/Avatar";
import { agentLabel } from "@/app/components/agentTheme";
import type { DagNode, DagSnapshot, NodeStatus } from "@/lib/api";

const STATUS_STYLE: Record<NodeStatus, string> = {
  pending: "border-border-soft bg-surface text-text-tertiary",
  ready: "border-border-soft bg-surface text-text-tertiary",
  running: "border-accent/40 bg-accent-soft/40 text-text-primary",
  succeeded: "border-success/30 bg-success/10 text-text-primary",
  failed: "border-danger/40 bg-danger/10 text-text-primary",
  skipped: "border-border-soft bg-surface text-text-tertiary line-through",
  cancelled: "border-border-soft bg-surface text-text-tertiary",
};

function StatusIcon({ status, attempts }: { status: NodeStatus; attempts: number }) {
  if (status === "running") return <Loader2 size={12} className="animate-spin text-accent" />;
  if (status === "succeeded") return <Check size={12} className="text-success" />;
  if (status === "failed") return <X size={12} className="text-danger" />;
  if (status === "skipped" || status === "cancelled") return <SkipForward size={12} />;
  if (attempts > 0) return <RotateCcw size={12} />;
  return <CircleDashed size={12} />;
}

function fmtMs(ms: number | null | undefined): string {
  if (!ms) return "";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** Levelize nodes by dependency depth so parallel branches sit side by side. */
function levels(nodes: DagNode[]): DagNode[][] {
  const depth: Record<string, number> = {};
  const byId: Record<string, DagNode> = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const depthOf = (id: string, seen: Set<string>): number => {
    if (id in depth) return depth[id];
    if (seen.has(id)) return 0; // defensive: cycles can't exist server-side
    seen.add(id);
    const node = byId[id];
    const d = node && node.deps.length ? Math.max(...node.deps.map((p) => depthOf(p, seen))) + 1 : 0;
    depth[id] = d;
    return d;
  };
  nodes.forEach((n) => depthOf(n.id, new Set()));
  const out: DagNode[][] = [];
  nodes.forEach((n) => {
    const d = depth[n.id] ?? 0;
    (out[d] ??= []).push(n);
  });
  return out.filter(Boolean);
}

/** Live execution-plan card. Every step is clickable — it expands to show
 * who did the work and exactly what they produced (plan, code, review
 * verdict), so no stage is ever a black box. */
export default function DagProgressCard({ dag }: { dag: DagSnapshot }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const rows = levels(dag.nodes);
  const done = dag.nodes.filter((n) => n.status === "succeeded").length;
  const open = openId ? dag.nodes.find((n) => n.id === openId) : undefined;

  return (
    <div className="ml-11 max-w-xl">
      <div className="rounded-xl border border-border-soft bg-surface px-4 py-3 shadow-sm">
        <div className="mb-2.5 flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
            Execution plan
          </span>
          <span className="text-[11px] text-text-tertiary">
            {done}/{dag.nodes.length} steps
            {dag.status === "running" ? "" : ` · ${dag.status}`}
          </span>
        </div>
        <div className="flex flex-col gap-1.5">
          {rows.map((row, ri) => (
            <div key={ri} className="flex flex-wrap items-center gap-1.5">
              {row.map((node) => (
                <button
                  key={node.id}
                  type="button"
                  onClick={() => setOpenId((v) => (v === node.id ? null : node.id))}
                  title={node.error || `${node.name} — click for details`}
                  className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition-shadow hover:shadow-sm ${
                    STATUS_STYLE[node.status] ?? STATUS_STYLE.pending
                  } ${openId === node.id ? "ring-1 ring-accent/50" : ""}`}
                >
                  <StatusIcon status={node.status} attempts={node.attempts} />
                  {node.name}
                  {node.attempts > 1 && <span className="text-text-tertiary">×{node.attempts}</span>}
                  {node.duration_ms ? <span className="text-text-tertiary">{fmtMs(node.duration_ms)}</span> : null}
                </button>
              ))}
              {ri < rows.length - 1 && <span className="sr-only">then</span>}
            </div>
          ))}
        </div>

        {open && (
          <div className="mt-3 rounded-lg border border-border-soft bg-bg/60">
            <div className="flex items-center gap-2 border-b border-border-soft px-3 py-2">
              <Avatar agent={open.agent} size={22} />
              <span className="text-xs font-semibold text-text-primary">{agentLabel(open.agent)}</span>
              <span className="text-[11px] text-text-tertiary">
                {open.status}
                {open.duration_ms ? ` · ${fmtMs(open.duration_ms)}` : ""}
              </span>
              <button
                type="button"
                onClick={() => setOpenId(null)}
                className="ml-auto text-text-tertiary transition-colors hover:text-text-primary"
                aria-label="Close step details"
              >
                <X size={13} />
              </button>
            </div>
            <div className="max-h-80 overflow-y-auto scroll-thin px-3 py-2.5">
              {open.error ? (
                <p className="text-xs text-danger">{open.error}</p>
              ) : open.output ? (
                <>
                  <Markdown>{String(open.output)}</Markdown>
                  {String(open.output).endsWith("[truncated]") && (
                    <p className="mt-2 text-[11px] text-text-tertiary">
                      Preview truncated — the full output is in the Files panel under docs/steps.
                    </p>
                  )}
                </>
              ) : (
                <p className="text-xs text-text-tertiary">
                  {open.status === "running" ? "Working — output appears here when this step finishes." : "No output yet."}
                </p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
