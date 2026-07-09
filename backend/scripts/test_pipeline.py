"""Phase 1 test: drive the graph through every gate, reject + fail loops included.

Run from backend/:  .venv/Scripts/python.exe -m scripts.test_pipeline
"""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")


def _interrupt_value(result):
    intr = result.get("__interrupt__")
    if not intr:
        return None
    first = intr[0]
    return getattr(first, "value", None) if not isinstance(first, dict) else first.get("value")


async def main() -> None:
    from langgraph.types import Command

    from app.orchestration.graph import get_graph
    from app.orchestration.events import read_events

    graph = get_graph()
    pid = "proj-phase1-test"
    config = {"configurable": {"thread_id": pid}}

    # Scripted human decisions, in the order the gates will be hit.
    responses = iter(
        [
            {"action": "reject", "feedback": "Add target users and success metric."},  # scope -> reject
            {"action": "approve"},                                                       # scope -> approve
            {"action": "approve"},                                                       # architecture
            {"action": "approve"},                                                       # tickets
            {"result": "fail", "feedback": "Health endpoint returns 500."},              # T-001 -> fail
            {"result": "pass"},                                                          # T-001 (after debug) -> pass
            {"result": "pass"},                                                          # T-002 -> pass
        ]
    )

    result = await graph.ainvoke({"project_id": pid, "raw_idea": "A todo app for teams."}, config)

    step = 0
    while (val := _interrupt_value(result)) is not None:
        step += 1
        gate = val.get("gate")
        kind = val.get("kind")
        print(f"[{step}] PAUSED at gate={gate} kind={kind} :: {val.get('summary')}")
        decision = next(responses)
        print(f"      -> human decides: {decision}")
        result = await graph.ainvoke(Command(resume=decision), config)

    snap = await graph.aget_state(config)
    print("\nFINAL status :", snap.values.get("status"))
    print("next nodes   :", snap.next)
    print("tickets      :", [t["id"] for t in snap.values.get("tickets", [])])
    print("review_rounds:", snap.values.get("review_rounds"))

    events = await read_events(pid, "0-0", block_ms=100)
    print(f"\nEvents published: {len(events)}")
    for _id, e in events[:6]:
        print(f"  - {e['kind']:14} {e['agent']:9} {e['message']}")
    print("  ...")

    assert snap.values.get("status") == "complete", "pipeline did not complete"
    assert not snap.next, "graph should be finished"
    print("\nPhase 1 PASSED: pause/resume, reject loop, and fail/debug loop all work.\n")


if __name__ == "__main__":
    asyncio.run(main())
