"""Phase 4 test: drive Scope -> Architect -> Planner with real Groq tool-calling.

Answers clarifying questions with a generic "use your judgment" reply so the
run terminates without a human in the loop, then prints the real scope doc,
architecture doc, and ticket list the agents produced.

Run from backend/:  .venv/Scripts/python.exe -m scripts.test_real_agents
"""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

from langgraph.types import Command  # noqa: E402

from app.orchestration.graph import get_graph  # noqa: E402

CONFIG = {"configurable": {"thread_id": "test-real-agents-2"}}
IDEA = "A mobile app that helps small independent cafes run digital loyalty punch cards for their regulars."
GENERIC_ANSWER = (
    "Use your best professional judgment and reasonable defaults for anything not specified. "
    "Target small independent cafes and their regular customers; the main goal is more repeat "
    "visits; there is no hard deadline or budget constraint and no mandated tech stack."
)
MAX_STEPS = 30


def _interrupt_value(result: dict) -> dict | None:
    intr = result.get("__interrupt__") if isinstance(result, dict) else None
    if not intr:
        return None
    first = intr[0]
    val = getattr(first, "value", None)
    if val is None and isinstance(first, dict):
        val = first.get("value")
    return val if isinstance(val, dict) else None


async def main() -> None:
    graph = get_graph()
    result = await graph.ainvoke({"project_id": "test-real-agents", "raw_idea": IDEA}, CONFIG)

    for step in range(1, MAX_STEPS + 1):
        intr = _interrupt_value(result)
        if intr is None:
            break

        kind = intr.get("kind")
        gate = intr.get("gate")
        payload = intr.get("payload", {})
        print(f"\n[{step}] gate={gate} kind={kind}")

        if kind == "clarify":
            for q in payload.get("questions", []):
                print(f"    Q: {q}")
            print(f"    -> {GENERIC_ANSWER[:70]}...")
            resume = Command(resume={"feedback": GENERIC_ANSWER})
        elif kind == "approval":
            print("    -> approve")
            resume = Command(resume={"action": "approve"})
        elif kind == "manual_test":
            print("    -> pass")
            resume = Command(resume={"result": "pass"})
        else:
            raise RuntimeError(f"unhandled gate kind: {kind}")

        result = await graph.ainvoke(resume, CONFIG)
    else:
        raise RuntimeError(f"exceeded {MAX_STEPS} steps without finishing tickets approval")

    snap = await graph.aget_state(CONFIG)
    values = snap.values
    print("\n" + "=" * 70)
    print("STATUS:", values.get("status"))
    print("\n--- SCOPE DOC ---\n", values.get("scope_doc"))
    print("\n--- ARCHITECTURE DOC ---\n", values.get("architecture_doc"))
    print("\n--- TICKETS ---")
    for t in values.get("tickets", []):
        print(f" - {t.get('id')}: {t.get('title')}  (deps={t.get('dependencies')}, effort={t.get('effort')})")
    print("\nPhase 4 test complete.")


if __name__ == "__main__":
    asyncio.run(main())
