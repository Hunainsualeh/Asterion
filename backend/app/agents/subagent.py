"""Sub-agents: short-lived specialists a parent agent spawns to do one job.

Modelled on OpenHands' `DelegateTool`. The insight worth stealing is that
delegation needs no orchestrator: it is *a tool like any other*. A parent agent
in its normal tool-calling loop calls `delegate(...)`, that tool runs N
independent tool loops concurrently, and returns their answers as one
observation. The parent's loop never knows anything unusual happened.

Contrast with Asterion's two existing execution models, neither of which this
replaces:

    orchestration/  a fixed six-stage SDLC pipeline with human gates
    dag/            a DAG planned up front by `exec_planner`, then executed

Both decide the shape of the work *before* the work starts. A sub-agent is
decided *during* — the Architect, halfway through reading a codebase, notices
three independent questions and fans them out.

Constraints that exist for a reason, all in `app.config`:

    max_subagents (5)          concurrent LLM requests, not total spawned. Groq's
                               free tier allows 30 RPM per key and a sub-agent
                               burns several per loop; wider fan-out converts
                               directly into 429s, model escalation, and worse
                               answers from the fallback tier.
    max_subagent_depth (2)     a sub-agent may delegate, but not forever. One
                               model that decides `delegate` is the answer to
                               everything would otherwise fork until every key in
                               the pool is on cooldown.
    subagent_max_iterations(6) a sub-agent has one narrow job.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.agents.base import ToolLoopExhausted, run_tool_loop
from app.config import get_settings
from app.llm.context import AgentContext
from app.tools.registry import ToolContext

log = logging.getLogger("asterion.subagent")

# Sub-agents report by calling this. It is their only terminal tool: a
# sub-agent's entire contract is "produce a written answer for your parent".
REPORT_TOOL = "report_result"


@dataclass(frozen=True)
class SubAgentSpec:
    """A named specialist. `agent` is the identity used everywhere else in the
    system — the tool allowlist in `tools/registry.py`, the model chain in
    `litellm_config.yaml`, the skill allowlist in `.agents/skills/`. Reusing it
    means a sub-agent gets exactly the capabilities its role already has, with
    no second permission system to keep in sync."""

    name: str
    agent: str
    description: str
    briefing: str

    def system_prompt(self, task: str) -> str:
        ctx = AgentContext(
            agent=self.agent,
            query=task,
            # The parent already loaded the catalog and passed down what
            # matters in `task`. Re-sending it to every child multiplies the
            # baseline cost of a fan-out by the number of children.
            include_skills=False,
        )
        return (
            f"{ctx.system_prompt()}\n\n"
            f"<SUBAGENT_ROLE>\n{self.briefing}\n\n"
            "You are a sub-agent working on ONE task delegated by a parent agent. "
            "Do that task and nothing else. You cannot ask the user anything — "
            "if information is missing, state the assumption you made and continue. "
            f"When you are done, call `{REPORT_TOOL}` with your findings. Your report "
            "is the ONLY thing your parent will see, so make it self-contained.\n"
            "</SUBAGENT_ROLE>"
        )


# The registry a parent picks from. Names are what the LLM writes, so they are
# short and describe a job, not an implementation.
SUBAGENTS: dict[str, SubAgentSpec] = {
    "researcher": SubAgentSpec(
        name="researcher",
        agent="research",
        description="Investigates a question using web search and the knowledge store. Returns cited findings.",
        briefing=(
            "Gather concrete, current facts. Cite sources inline. Distinguish what you verified "
            "from what you inferred. If the evidence is thin, say so rather than padding."
        ),
    ),
    "code_reader": SubAgentSpec(
        name="code_reader",
        agent="reviewer",
        description="Reads and explains existing code in the workspace. Returns a structured summary of how something works.",
        briefing=(
            "Read the relevant files before saying anything about them. Quote the lines that "
            "support each claim, with file paths. Never guess at code you have not opened."
        ),
    ),
    "coder": SubAgentSpec(
        name="coder",
        agent="developer",
        description="Implements one self-contained change in the workspace. Returns what it wrote and why.",
        briefing=(
            "Write working code, not a sketch. Match the conventions of the files around you. "
            "Report every file you created or modified, and anything you deliberately left undone."
        ),
    ),
    "critic": SubAgentSpec(
        name="critic",
        agent="reviewer",
        description="Reviews a plan, a design, or a diff and reports concrete problems. Returns findings, most severe first.",
        briefing=(
            "Find real defects, not style opinions. For each finding give the specific input or "
            "condition under which it fails. If you find nothing, say so — do not invent filler."
        ),
    ),
    "tester": SubAgentSpec(
        name="tester",
        agent="test",
        description="Writes or runs tests for a change and reports what passed and what failed.",
        briefing=(
            "Run the tests; do not predict their outcome. Report exact failures with their output. "
            "A test you did not run is not a test that passed."
        ),
    ),
    "analyst": SubAgentSpec(
        name="analyst",
        agent="analyze",
        description="Breaks down a question, compares options, and returns a reasoned recommendation.",
        briefing=(
            "State the criteria before the conclusion. Give the strongest case for the option you "
            "reject. End with one clear recommendation, not a survey."
        ),
    ),
}


def catalog() -> str:
    """Rendered into the `delegate` tool's own description, so the parent model
    learns the roster from the tool schema rather than the system prompt."""
    return "\n".join(f"- {s.name}: {s.description}" for s in SUBAGENTS.values())


@dataclass
class SubAgentResult:
    name: str
    task: str
    ok: bool
    result: str
    iterations: int = 0


async def _run_one(
    parent: ToolContext,
    spec: SubAgentSpec,
    task: str,
    semaphore: asyncio.Semaphore,
    depth: int,
) -> SubAgentResult:
    settings = get_settings()
    # The child shares the parent's project (same workspace, same repo) but
    # carries its own agent identity, so `tools.registry.dispatch` grants it the
    # specialist's allowlist rather than the parent's.
    ctx = ToolContext(
        project_id=parent.project_id,
        agent=spec.agent,
        extra={**parent.extra, "subagent": spec.name, "depth": depth},
    )

    async with semaphore:
        try:
            loop = await run_tool_loop(
                ctx,
                system_prompt=spec.system_prompt(task),
                user_messages=[{"role": "user", "content": task}],
                terminal_tools={REPORT_TOOL},
                max_iterations=settings.subagent_max_iterations,
            )
        except ToolLoopExhausted:
            # The child used its whole budget without reporting. That is a real
            # answer for the parent — "this was too big for one sub-agent" — not
            # a reason to fail the parent's run.
            log.warning("subagent %s exhausted its iteration budget on: %s", spec.name, task[:80])
            return SubAgentResult(
                name=spec.name, task=task, ok=False,
                result=f"Ran out of steps after {settings.subagent_max_iterations} iterations without reaching a conclusion.",
            )
        except Exception as exc:  # noqa: BLE001 — one child must not kill the fan-out
            log.warning("subagent %s failed: %s", spec.name, exc)
            return SubAgentResult(name=spec.name, task=task, ok=False, result=f"Failed: {exc}")

    report = str(loop.args.get("result") or loop.args.get("summary") or "").strip()
    return SubAgentResult(
        name=spec.name, task=task, ok=bool(report),
        result=report or "Reported no findings.", iterations=loop.iterations,
    )


async def run_many(
    parent: ToolContext,
    assignments: list[tuple[str, str]],
) -> list[SubAgentResult]:
    """Run `(subagent_name, task)` pairs concurrently, bounded by `max_subagents`.

    Every assignment produces a result — a failed child returns its failure as
    text rather than raising, because a parent that got 4 of 5 answers should
    keep working with 4.
    """
    settings = get_settings()
    depth = int(parent.extra.get("depth", 0)) + 1
    if depth > settings.max_subagent_depth:
        return [
            SubAgentResult(name=n, task=t, ok=False,
                           result=f"Delegation depth limit ({settings.max_subagent_depth}) reached — do this task yourself.")
            for n, t in assignments
        ]

    unknown = [n for n, _ in assignments if n not in SUBAGENTS]
    if unknown:
        available = ", ".join(SUBAGENTS)
        return [
            SubAgentResult(name=n, task=t, ok=False, result=f"No such sub-agent '{n}'. Available: {available}")
            if n in unknown
            else SubAgentResult(name=n, task=t, ok=False, result="Skipped: another assignment named an unknown sub-agent.")
            for n, t in assignments
        ]

    semaphore = asyncio.Semaphore(max(1, settings.max_subagents))
    log.info(
        "Spawning %d subagent(s) at depth %d, %d at a time: %s",
        len(assignments), depth, settings.max_subagents, ", ".join(n for n, _ in assignments),
    )
    return await asyncio.gather(
        *(_run_one(parent, SUBAGENTS[name], task, semaphore, depth) for name, task in assignments)
    )
