"""Production-grade async DAG execution engine.

The unit of work is a `NodeSpec`; a validated set of them forms a `DagSpec`.
`DagRun.execute()` schedules every node the moment its dependencies finish —
independent nodes run concurrently (bounded by a semaphore so a wide fan-out
can't stampede the LLM rate limits). Each node gets retries with exponential
backoff, a hard timeout, and a terminal status; a permanently failed node
skips its downstream subtree unless the dependent opted into partial inputs
(`allow_failed_deps`, e.g. a summarizer that should still produce *something*
from whatever succeeded).

Everything observable — per-node status, attempts, timings, outputs, errors —
is snapshotted to Redis on every transition, so the API can serve live DAG
state and full execution history without touching the run's in-process
objects. Progress is also published to the project's event stream for the UI.

Cycle prevention is structural: `DagSpec.validate()` runs Kahn's algorithm and
refuses to construct a spec with a cycle, an unknown dependency, or a
duplicate id — including when a planner node dynamically expands the graph
mid-run (`Expansion` outputs are re-validated against the live DAG).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.redis.client import get_redis, key

# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------
PENDING = "pending"        # waiting on dependencies
READY = "ready"            # deps met, queued for a worker slot
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"          # exhausted retries
SKIPPED = "skipped"        # an upstream dependency failed/was cancelled
CANCELLED = "cancelled"

TERMINAL = {SUCCEEDED, FAILED, SKIPPED, CANCELLED}

DEFAULT_NODE_TIMEOUT_S = 240.0
DEFAULT_CONCURRENCY = 4


class DagValidationError(ValueError):
    """Raised for cycles, unknown dependencies, or duplicate node ids."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2          # total attempts (1 = no retry)
    backoff_base_s: float = 2.0    # sleep = base * 2^(attempt-1), capped
    backoff_max_s: float = 20.0

    def delay(self, attempt: int) -> float:
        return min(self.backoff_base_s * (2 ** (attempt - 1)), self.backoff_max_s)


@dataclass
class NodeSpec:
    id: str
    agent: str                      # executor name (see workflows.EXECUTORS)
    name: str = ""                  # human label; defaults to id
    params: dict[str, Any] = field(default_factory=dict)
    deps: list[str] = field(default_factory=list)
    timeout_s: float = DEFAULT_NODE_TIMEOUT_S
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    # If True this node still runs when some deps failed/skipped — it receives
    # whatever upstream outputs exist. For fan-in nodes that should degrade
    # gracefully instead of dying with the first failed branch.
    allow_failed_deps: bool = False
    # The node whose output is the run's final result. Defaults to the last
    # node in topological order if none is marked.
    is_final: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.id


@dataclass
class NodeRun:
    spec: NodeSpec
    status: str = PENDING
    attempts: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    output: Any = None
    error: str = ""

    @property
    def duration_ms(self) -> int | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at) * 1000)

    def snapshot(self) -> dict[str, Any]:
        out = self.output
        # Keep snapshots JSON-safe and bounded; full outputs live on the run
        # result object handed to the caller.
        if isinstance(out, str) and len(out) > 4000:
            out = out[:4000] + "\n...[truncated]"
        elif out is not None and not isinstance(out, (str, int, float, bool, dict, list)):
            out = str(out)[:4000]
        return {
            "id": self.spec.id,
            "name": self.spec.name,
            "agent": self.spec.agent,
            "deps": self.spec.deps,
            "status": self.status,
            "attempts": self.attempts,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "output": out,
            "error": self.error,
            "is_final": self.spec.is_final,
        }


class DagSpec:
    """A validated, acyclic set of nodes."""

    def __init__(self, nodes: list[NodeSpec]):
        self.nodes: dict[str, NodeSpec] = {}
        for n in nodes:
            if n.id in self.nodes:
                raise DagValidationError(f"duplicate node id: {n.id}")
            self.nodes[n.id] = n
        self.topo_order = self._validate()

    def _validate(self) -> list[str]:
        """Kahn's algorithm: returns a topological order or raises."""
        indegree: dict[str, int] = {nid: 0 for nid in self.nodes}
        for n in self.nodes.values():
            for dep in n.deps:
                if dep not in self.nodes:
                    raise DagValidationError(f"node '{n.id}' depends on unknown node '{dep}'")
                if dep == n.id:
                    raise DagValidationError(f"node '{n.id}' depends on itself")
                indegree[n.id] += 1
        queue = [nid for nid, d in indegree.items() if d == 0]
        order: list[str] = []
        while queue:
            nid = queue.pop()
            order.append(nid)
            for m in self.nodes.values():
                if nid in m.deps:
                    indegree[m.id] -= 1
                    if indegree[m.id] == 0:
                        queue.append(m.id)
        if len(order) != len(self.nodes):
            cyclic = sorted(set(self.nodes) - set(order))
            raise DagValidationError(f"cycle detected involving nodes: {', '.join(cyclic)}")
        return order


@dataclass
class Expansion:
    """A node executor may return this to splice new nodes into the live DAG
    (dynamic DAG generation — e.g. a planner fanning out subtasks). `result`
    is recorded as the emitting node's own output."""

    nodes: list[NodeSpec]
    result: Any = None


@dataclass
class ExecutionContext:
    """What an executor sees: the project, the run, its params, and every
    upstream output produced so far."""

    project_id: str
    run_id: str
    query: str
    node: NodeSpec
    outputs: dict[str, Any]                 # node_id -> output (all finished nodes)
    meta: dict[str, Any] = field(default_factory=dict)

    def dep_outputs(self) -> dict[str, Any]:
        return {d: self.outputs.get(d) for d in self.node.deps if d in self.outputs}


Executor = Callable[[ExecutionContext], Awaitable[Any]]
Publisher = Callable[..., Awaitable[None]]  # publish(kind, agent, message, data)


# ---------------------------------------------------------------------------
# Redis persistence (live state + history)
# ---------------------------------------------------------------------------
def _run_key(project_id: str, run_id: str) -> str:
    return key("dagrun", project_id, run_id)

def _runs_index_key(project_id: str) -> str:
    return key("dagruns", project_id)


async def save_run_snapshot(snapshot: dict[str, Any]) -> None:
    r = await get_redis()
    pid, rid = snapshot["project_id"], snapshot["run_id"]
    await r.set(_run_key(pid, rid), json.dumps(snapshot, default=str))
    # newest-first index, bounded
    await r.lrem(_runs_index_key(pid), 0, rid)
    await r.lpush(_runs_index_key(pid), rid)
    await r.ltrim(_runs_index_key(pid), 0, 49)


async def load_run(project_id: str, run_id: str) -> dict[str, Any] | None:
    r = await get_redis()
    raw = await r.get(_run_key(project_id, run_id))
    if raw is None:
        return None
    return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


async def list_runs(project_id: str) -> list[dict[str, Any]]:
    r = await get_redis()
    rids = [x.decode() if isinstance(x, bytes) else x for x in await r.lrange(_runs_index_key(project_id), 0, -1)]
    out = []
    for rid in rids:
        run = await load_run(project_id, rid)
        if run:
            out.append(run)
    return out


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
class DagRun:
    def __init__(
        self,
        project_id: str,
        spec: DagSpec,
        query: str,
        executors: dict[str, Executor],
        publish: Publisher,
        *,
        run_id: str | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        label: str = "",
        meta: dict[str, Any] | None = None,
    ):
        self.project_id = project_id
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:10]}"
        self.query = query
        self.label = label
        self.meta = meta or {}
        self.spec = spec
        self.executors = executors
        self.publish = publish
        self.nodes: dict[str, NodeRun] = {nid: NodeRun(spec=n) for nid, n in spec.nodes.items()}
        self.status = PENDING
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.outputs: dict[str, Any] = {}
        self.final_output: Any = None
        self.error: str = ""
        self._sem = asyncio.Semaphore(concurrency)
        self._cancel_event = asyncio.Event()
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._wake = asyncio.Event()  # set whenever a node reaches a terminal state

    # ---- public controls ----
    def cancel(self) -> None:
        self._cancel_event.set()
        for task in self._running_tasks.values():
            task.cancel()
        self._wake.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ---- state helpers ----
    def snapshot(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "label": self.label,
            "query": self.query,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": int((self.finished_at - self.started_at) * 1000)
            if self.started_at and self.finished_at
            else None,
            "error": self.error,
            "nodes": [self.nodes[nid].snapshot() for nid in self._display_order()],
            "edges": [
                {"from": dep, "to": n.id} for n in self.spec.nodes.values() for dep in n.deps
            ],
        }

    def _display_order(self) -> list[str]:
        # topo order is stable + readable for the UI; refresh in case of expansion
        return self.spec.topo_order

    async def _persist(self) -> None:
        # Observability persistence must never take down the run itself — a
        # Redis blip loses a snapshot, not the execution.
        try:
            await save_run_snapshot(self.snapshot())
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("asterion.dag").warning(
                "failed to persist DAG snapshot for %s", self.run_id, exc_info=True
            )

    async def _publish_node(self, kind: str, node: NodeRun, message: str = "") -> None:
        await self.publish(
            self.project_id,
            kind,
            node.spec.agent,
            message or f"{node.spec.name}: {node.status}",
            {"run_id": self.run_id, "node": node.snapshot(), "dag": self.progress_payload()},
        )

    def progress_payload(self) -> dict[str, Any]:
        """Compact whole-DAG status for every event, so any single SSE frame
        is enough to render current progress (no client-side event joins)."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "nodes": [
                {
                    "id": nr.spec.id,
                    "name": nr.spec.name,
                    "agent": nr.spec.agent,
                    "deps": nr.spec.deps,
                    "status": nr.status,
                    "attempts": nr.attempts,
                    "duration_ms": nr.duration_ms,
                }
                for nr in (self.nodes[nid] for nid in self._display_order())
            ],
        }

    # ---- scheduling core ----
    def _deps_status(self, spec: NodeSpec) -> str:
        """'ready' | 'wait' | 'blocked' for a pending node."""
        failed = 0
        for dep in spec.deps:
            st = self.nodes[dep].status
            if st in (FAILED, SKIPPED, CANCELLED):
                failed += 1
            elif st != SUCCEEDED:
                return "wait"
        if failed == 0:
            return "ready"
        if spec.allow_failed_deps:
            # runs with partial inputs as long as at least one dep succeeded,
            # or it has no succeeded deps but we still prefer running the
            # fan-in over silently dropping the whole run's tail.
            return "ready"
        return "blocked"

    async def execute(self) -> "DagRun":
        self.status = RUNNING
        self.started_at = time.time()
        await self.publish(
            self.project_id,
            "dag_started",
            "orchestrator",
            f"Execution plan started ({len(self.nodes)} steps)",
            {"run_id": self.run_id, "dag": self.progress_payload()},
        )
        await self._persist()

        try:
            while True:
                if self.cancelled:
                    break
                # Clear the wake flag *before* scanning, so a completion that
                # lands mid-scan re-triggers the loop instead of being lost.
                self._wake.clear()
                while await self._launch_ready():
                    pass  # settle cascading launches/skips in one pass
                unfinished = [n for n in self.nodes.values() if n.status not in TERMINAL]
                if not unfinished:
                    break
                if not self._running_tasks:
                    # Nothing running and nothing launchable: every remaining
                    # node is blocked behind a failure — skip them all.
                    for nr in unfinished:
                        nr.status = SKIPPED
                        nr.error = "upstream dependency failed"
                        nr.finished_at = time.time()
                        await self._publish_node("node_skipped", nr)
                    break
                await self._wake.wait()

            # cancellation cleanup
            if self.cancelled:
                for task in self._running_tasks.values():
                    task.cancel()
                if self._running_tasks:
                    await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)
                for nr in self.nodes.values():
                    if nr.status not in TERMINAL:
                        nr.status = CANCELLED
                        nr.finished_at = nr.finished_at or time.time()

            self._finalize()
        finally:
            self.finished_at = time.time()
            await self._persist()
            await self.publish(
                self.project_id,
                "dag_finished",
                "orchestrator",
                f"Execution plan {self.status}",
                {
                    "run_id": self.run_id,
                    "status": self.status,
                    "error": self.error,
                    "dag": self.progress_payload(),
                },
            )
        return self

    async def _launch_ready(self) -> bool:
        changed = False
        for nid in list(self.spec.topo_order):
            nr = self.nodes[nid]
            if nr.status != PENDING:
                continue
            verdict = self._deps_status(nr.spec)
            if verdict == "ready":
                nr.status = READY
                task = asyncio.create_task(self._run_node(nr))
                self._running_tasks[nid] = task
                task.add_done_callback(lambda t, i=nid: self._on_task_done(i))
                changed = True
            elif verdict == "blocked":
                nr.status = SKIPPED
                nr.error = "upstream dependency failed"
                nr.finished_at = time.time()
                await self._publish_node("node_skipped", nr)
                changed = True  # state changed; re-evaluate dependents
        return changed

    def _on_task_done(self, nid: str) -> None:
        self._running_tasks.pop(nid, None)
        self._wake.set()

    async def _run_node(self, nr: NodeRun) -> None:
        spec = nr.spec
        executor = self.executors.get(spec.agent)
        if executor is None:
            nr.status = FAILED
            nr.error = f"no executor registered for agent '{spec.agent}'"
            nr.finished_at = time.time()
            await self._publish_node("node_failed", nr, f"{spec.name}: {nr.error}")
            await self._persist()
            return

        async with self._sem:
            nr.status = RUNNING
            nr.started_at = time.time()
            await self._publish_node("node_started", nr, f"{spec.name} started")
            await self._persist()

            policy = spec.retry
            while True:
                nr.attempts += 1
                try:
                    ctx = ExecutionContext(
                        project_id=self.project_id,
                        run_id=self.run_id,
                        query=self.query,
                        node=spec,
                        outputs=dict(self.outputs),
                        meta=self.meta,
                    )
                    result = await asyncio.wait_for(executor(ctx), timeout=spec.timeout_s)
                    if isinstance(result, Expansion):
                        expand_error = self._apply_expansion(spec.id, result)
                        if expand_error:
                            raise DagValidationError(expand_error)
                        result = result.result
                    nr.output = result
                    self.outputs[spec.id] = result
                    nr.status = SUCCEEDED
                    nr.finished_at = time.time()
                    await self._publish_node("node_finished", nr, f"{spec.name} finished")
                    await self._persist()
                    return
                except asyncio.CancelledError:
                    nr.status = CANCELLED
                    nr.finished_at = time.time()
                    nr.error = "cancelled"
                    await self._persist()
                    raise
                except Exception as exc:  # noqa: BLE001 — node failures must not kill the scheduler
                    err = f"{exc.__class__.__name__}: {exc}"
                    if nr.attempts < policy.max_attempts and not self.cancelled:
                        delay = policy.delay(nr.attempts)
                        nr.error = err
                        await self._publish_node(
                            "node_retry",
                            nr,
                            f"{spec.name} failed (attempt {nr.attempts}/{policy.max_attempts}), retrying in {delay:.0f}s",
                        )
                        await asyncio.sleep(delay)
                        continue
                    nr.status = FAILED
                    nr.error = err
                    nr.finished_at = time.time()
                    await self._publish_node("node_failed", nr, f"{spec.name} failed: {err}")
                    await self._persist()
                    return

    def _apply_expansion(self, source_id: str, expansion: Expansion) -> str:
        """Splice dynamically generated nodes into the live DAG. New nodes may
        depend on any existing node; existing nodes are never mutated, so a
        cycle can only exist within the new set — re-validate the whole graph
        to be safe. Returns an error string instead of raising so the caller
        can attribute it to the emitting node."""
        if not expansion.nodes:
            return ""
        try:
            combined = list(self.spec.nodes.values())
            for new in expansion.nodes:
                if not new.deps:
                    new.deps = [source_id]  # default: run after the expanding node
                combined.append(new)
            new_spec = DagSpec(combined)
        except DagValidationError as exc:
            return f"invalid dynamic expansion: {exc}"
        self.spec = new_spec
        for n in expansion.nodes:
            self.nodes[n.id] = NodeRun(spec=n)
        # wake the scheduler so the new nodes get considered
        self._wake.set()
        return ""

    def _finalize(self) -> None:
        statuses = {nr.status for nr in self.nodes.values()}
        if self.cancelled:
            self.status = CANCELLED
        elif FAILED in statuses or SKIPPED in statuses:
            # the run "succeeded with degradation" if a final output exists
            self.status = SUCCEEDED if self._pick_final_output() is not None else FAILED
        else:
            self.status = SUCCEEDED

        self.final_output = self._pick_final_output()
        if self.status == FAILED and not self.error:
            failed = [nr for nr in self.nodes.values() if nr.status == FAILED]
            self.error = "; ".join(f"{nr.spec.name}: {nr.error}" for nr in failed[:3])

    def _pick_final_output(self) -> Any:
        finals = [nr for nr in self.nodes.values() if nr.spec.is_final and nr.status == SUCCEEDED]
        if finals:
            return finals[-1].output
        # fallback: last succeeded node in topo order
        for nid in reversed(self.spec.topo_order):
            if self.nodes[nid].status == SUCCEEDED:
                return self.nodes[nid].output
        return None
