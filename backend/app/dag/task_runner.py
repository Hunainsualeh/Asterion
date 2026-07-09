"""Task-lane runner: executes a DAG for a classified (non-SDLC) request.

The counterpart of `app.orchestration.runner` for the DAG engine. Owns the
in-process registry of live runs (for cancellation), persists the final
result both to Redis (instant API reads) and to the project workspace as a
markdown artifact (durable, browsable), and guarantees the chat stream always
ends with either a `result` event carrying the actual deliverable or an
`error` event explaining why there isn't one — a run can no longer finish
silently.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

from app.dag.engine import DagRun, DagSpec, DagValidationError
from app.dag.workflows import EXECUTORS, build_deep_research_nodes, build_nodes_for
from app.orchestration.events import publish_event
from app.orchestration.intent import Intent
from app.orchestration.stages import describe_error
from app.services import project_store as store
from app.tools.registry import ToolContext

log = logging.getLogger("asterion.dag.runner")

_runs: dict[str, DagRun] = {}
_tasks: dict[str, asyncio.Task] = {}


def active_run(pid: str) -> DagRun | None:
    return _runs.get(pid)


async def start(pid: str, query: str, intent: Intent, context: str = "", deep_research: bool = False) -> None:
    """Build the DAG for this intent and launch it in the background.
    `context` carries prior conversation turns for follow-up messages.
    `deep_research` forces the multi-step research→report DAG."""
    if intent.questions and not deep_research:
        # Essential decisions are missing — ask instead of guessing. The next
        # user message is merged with this request and runs immediately
        # (see the /message endpoint), so this costs one round-trip, not a form.
        qs = "\n".join(f"- {q}" for q in intent.questions)
        text = (
            "Quick check before I build this:\n\n"
            f"{qs}\n\n"
            "Reply here — or say “you decide” and I'll pick sensible defaults."
        )
        await store.set_clarify(pid, query)
        await store.set_status(pid, "complete")  # keeps the composer open
        await store.append_history(pid, "assistant", text)
        await publish_event(
            pid, "result", "orchestrator", "Clarification needed",
            {"result": text, "questions": intent.questions, "intro": "Quick check before I build this:"},
        )
        await publish_event(pid, "done", "system", "Awaiting details", {"lane": "task"})
        return

    try:
        nodes = build_deep_research_nodes(query) if deep_research else build_nodes_for(intent, query)
        spec = DagSpec(nodes)
    except DagValidationError as exc:
        await store.set_status(pid, "error")
        await publish_event(pid, "error", "orchestrator", f"Invalid execution plan: {exc}",
                            {"friendly_error": asdict(describe_error(exc))})
        return

    run = DagRun(pid, spec, query, EXECUTORS, publish_event, label=intent.kind,
                 meta={"history": context} if context else None)
    _runs[pid] = run
    task = asyncio.create_task(_execute(pid, run))
    _tasks[pid] = task
    task.add_done_callback(lambda t: (_tasks.pop(pid, None), _runs.pop(pid, None)))


def cancel(pid: str) -> bool:
    run = _runs.get(pid)
    if run is None:
        return False
    run.cancel()
    return True


async def _execute(pid: str, run: DagRun) -> None:
    await store.set_running(pid, True)
    await store.set_status(pid, "running")
    try:
        await run.execute()
    except Exception as exc:  # noqa: BLE001 — engine bugs must still surface to the user
        log.exception("DAG run crashed for %s", pid)
        friendly = describe_error(exc)
        await store.set_status(pid, "error")
        await publish_event(pid, "error", "system",
                            f"Run failed ({friendly.reference}): {exc.__class__.__name__}: {exc}",
                            {"friendly_error": asdict(friendly)})
        return
    finally:
        await store.set_running(pid, False)

    if run.status == "cancelled":
        await store.set_status(pid, "cancelled")
        await publish_event(pid, "cancelled", "system", "Run cancelled")
        return

    final = run.final_output
    if run.status == "succeeded" and final:
        result_text = final if isinstance(final, str) else str(final)
        await _persist_result(pid, run, result_text)
        await store.set_status(pid, "complete")
        await store.append_history(pid, "assistant", result_text)
        await publish_event(pid, "result", "orchestrator", "Final result ready",
                            {"run_id": run.run_id, "result": result_text, "dag": run.progress_payload()})
        await publish_event(pid, "done", "system", "Task complete", {"lane": "task"})
        return

    # Failure recovery: never end with silence. Salvage partial outputs if any
    # node succeeded; otherwise emit a real error with the failure reasons.
    partials = [
        f"### {nr.spec.name}\n{nr.output}"
        for nr in run.nodes.values()
        if nr.status == "succeeded" and isinstance(nr.output, str) and nr.output.strip()
    ]
    if partials:
        salvage = (
            "I couldn't finish every step, but here is what I did complete:\n\n"
            + "\n\n".join(partials)
            + f"\n\n---\n_Incomplete because: {run.error or 'some steps failed'}_"
        )
        await _persist_result(pid, run, salvage)
        await store.set_status(pid, "complete_partial")
        await store.append_history(pid, "assistant", salvage)
        await publish_event(pid, "result", "orchestrator", "Partial result ready",
                            {"run_id": run.run_id, "result": salvage, "partial": True,
                             "dag": run.progress_payload()})
        return

    friendly = describe_error(RuntimeError(run.error or "all steps failed"))
    await store.set_status(pid, "error")
    await publish_event(pid, "error", "system", f"Run failed: {run.error}",
                        {"friendly_error": asdict(friendly)})


async def _persist_result(pid: str, run: DagRun, result_text: str) -> None:
    """Result goes to Redis (API) and the workspace docs dir (durable file)."""
    await store.set_result(pid, {
        "run_id": run.run_id,
        "status": run.status,
        "result": result_text,
        "label": run.label,
    })
    try:
        docs = ToolContext(project_id=pid, agent="orchestrator").docs_dir
        (docs / "result.md").write_text(result_text, encoding="utf-8")
        nodes_dir = docs / "steps"
        nodes_dir.mkdir(exist_ok=True)
        for nr in run.nodes.values():
            if isinstance(nr.output, str) and nr.output.strip():
                (nodes_dir / f"{nr.spec.id}.md").write_text(
                    f"# {nr.spec.name} ({nr.status})\n\n{nr.output}", encoding="utf-8"
                )
    except OSError as exc:
        log.warning("couldn't write result artifacts for %s: %s", pid, exc)
