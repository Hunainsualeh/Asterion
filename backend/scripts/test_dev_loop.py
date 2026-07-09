"""Phase 5/6 test: drive the full pipeline including the Developer/Reviewer/
Debugger loop with real Groq tool-calling, real local git, and real shell
commands inside workspace/<project_id>/repo.

The first manual test is deliberately FAILed once to exercise the Debugger,
then everything is PASSed. Uses a small, dependency-free idea so the
Developer's own `run_command` checks (plain `python`) actually succeed here.

Run from backend/:  .venv/Scripts/python.exe -m scripts.test_dev_loop
"""
from __future__ import annotations

import asyncio
import logging
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

from langgraph.types import Command  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.orchestration.graph import get_graph  # noqa: E402

PROJECT_ID = "test-dev-loop-7"
CONFIG = {"configurable": {"thread_id": PROJECT_ID}}
IDEA = (
    "A single-file Python command-line tool, temp_convert.py, that converts a temperature "
    "value between Celsius and Fahrenheit. Usage: `python temp_convert.py 100 C` prints the "
    "value in Fahrenheit. No external dependencies, no network, no database — pure stdlib."
)
GENERIC_ANSWER = (
    "Use your best judgment. Keep it a single dependency-free Python script runnable with "
    "the standard library only. No deadline or budget constraint."
)
MAX_STEPS = 60


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
    result = await graph.ainvoke({"project_id": PROJECT_ID, "raw_idea": IDEA}, CONFIG)
    failed_once = False

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
            resume = Command(resume={"feedback": GENERIC_ANSWER})
        elif kind == "approval":
            print("    -> approve")
            resume = Command(resume={"action": "approve"})
        elif kind == "manual_test":
            ticket = payload.get("ticket", {})
            print(f"    ticket={ticket.get('id')} branch={payload.get('branch')}")
            if not failed_once:
                failed_once = True
                print("    -> FAIL (exercising the Debugger loop)")
                resume = Command(
                    resume={"result": "fail", "feedback": "Running `python temp_convert.py 100 C` raises a NameError instead of printing 212."}
                )
            else:
                print("    -> pass")
                resume = Command(resume={"result": "pass"})
        else:
            raise RuntimeError(f"unhandled gate kind: {kind}")

        result = await graph.ainvoke(resume, CONFIG)
    else:
        raise RuntimeError(f"exceeded {MAX_STEPS} steps without completing")

    snap = await graph.aget_state(CONFIG)
    values = snap.values
    print("\n" + "=" * 70)
    print("STATUS:", values.get("status"))
    print("\n--- TICKETS ---")
    for t in values.get("tickets", []):
        print(f" - {t.get('id')}: {t.get('title')}  status={t.get('status')}")

    repo_dir = get_settings().workspace_dir / PROJECT_ID / "repo"
    docs_dir = get_settings().workspace_dir / PROJECT_ID / "docs"
    print(f"\n--- REPO FILES ({repo_dir}) ---")
    if repo_dir.exists():
        for p in sorted(repo_dir.rglob("*")):
            if ".git" not in p.parts and p.is_file():
                print(" -", p.relative_to(repo_dir))
    print(f"\n--- DOCS ({docs_dir}) ---")
    if docs_dir.exists():
        for p in sorted(docs_dir.glob("*")):
            print(" -", p.name)

    print("\nPhase 5/6 test complete.")


if __name__ == "__main__":
    asyncio.run(main())
