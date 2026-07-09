"""Pipeline runner.

Executes the graph in the background until it either hits a human gate
(interrupt) or completes, then records where it paused so the API/UI can pick
it up. A per-project lock serializes runs so a project can't advance two ways
at once.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any

from langgraph.types import Command

from app.orchestration.events import publish_event
from app.orchestration.graph import get_graph
from app.orchestration.stages import describe_error
from app.services import project_store as store

log = logging.getLogger("asterion.runner")

_locks: dict[str, asyncio.Lock] = {}
_tasks: dict[str, asyncio.Task] = {}


def _lock(pid: str) -> asyncio.Lock:
    if pid not in _locks:
        _locks[pid] = asyncio.Lock()
    return _locks[pid]


def _config(pid: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": pid}}


def _interrupt_value(result: dict[str, Any]) -> dict[str, Any] | None:
    intr = result.get("__interrupt__") if isinstance(result, dict) else None
    if not intr:
        return None
    first = intr[0]
    val = getattr(first, "value", None)
    if val is None and isinstance(first, dict):
        val = first.get("value")
    return val if isinstance(val, dict) else None


async def _run(pid: str, graph_input: Any) -> None:
    """Run the graph to the next gate/completion. Assumes the lock is held."""
    graph = get_graph()
    await store.set_running(pid, True)
    await store.set_status(pid, "running")  # keep status truthful mid-run (was: stale gate/error label)
    await store.clear_pending(pid)
    await publish_event(pid, "running", "system", "Pipeline running")
    try:
        result = await graph.ainvoke(graph_input, _config(pid))
    except asyncio.CancelledError:
        # User-requested stop. The checkpointer has everything up to the last
        # completed node, so /retry can continue from here later.
        await store.set_status(pid, "cancelled")
        await publish_event(pid, "cancelled", "system", "Pipeline stopped by user")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("Pipeline run failed for %s", pid)
        friendly = describe_error(exc)
        await store.set_status(pid, "error")
        await publish_event(
            pid,
            "error",
            "system",
            f"Pipeline error ({friendly.reference}): {exc.__class__.__name__}: {exc}",
            {"friendly_error": asdict(friendly)},
        )
        return
    finally:
        await store.set_running(pid, False)

    interrupt_value = _interrupt_value(result)
    if interrupt_value is not None:
        gate = interrupt_value.get("gate", "")
        await store.set_pending(pid, interrupt_value)
        await store.set_status(pid, f"awaiting:{gate}")
        await publish_event(pid, "awaiting_input", "system", f"Waiting for {gate}", interrupt_value)
    else:
        snap = await graph.aget_state(_config(pid))
        status = snap.values.get("status", "complete") if snap else "complete"
        await store.set_status(pid, status)
        await store.clear_pending(pid)


async def _guarded(pid: str, graph_input: Any) -> None:
    async with _lock(pid):
        await _run(pid, graph_input)


def _launch(pid: str, graph_input: Any) -> None:
    """Fire-and-forget a graph run; keep a reference so it isn't GC'd."""
    task = asyncio.create_task(_guarded(pid, graph_input))
    _tasks[pid] = task
    task.add_done_callback(lambda t: _tasks.pop(pid, None))


async def start(pid: str, raw_idea: str) -> None:
    _launch(pid, {"project_id": pid, "raw_idea": raw_idea})


async def resume(pid: str, decision: dict[str, Any]) -> None:
    _launch(pid, Command(resume=decision))


async def retry(pid: str) -> None:
    """Re-run after an error, continuing from the last successful checkpoint.

    LangGraph checkpoints after every node completes, so `ainvoke(None, ...)`
    picks up exactly where things left off — including re-running whichever
    node was mid-flight when it raised. The one gap: if the very first node
    (scope) never completed, no checkpoint exists yet for this thread, so
    there's nothing to continue from — fall back to the original start input.
    """
    snap = await get_graph().aget_state(_config(pid))
    if snap and snap.values:
        _launch(pid, None)
        return
    proj = await store.get_project(pid)
    _launch(pid, {"project_id": pid, "raw_idea": (proj or {}).get("idea", "")})


def cancel(pid: str) -> bool:
    """Cancel the in-flight graph run for this project, if any."""
    task = _tasks.get(pid)
    if task is None or task.done():
        return False
    task.cancel()
    return True


async def snapshot(pid: str):
    return await get_graph().aget_state(_config(pid))
