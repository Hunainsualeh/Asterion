"""AgentContext — composes the system prompt an agent actually sees.

Before this module, `prompts.load(agent)` returned a static string: the role
prompt plus the whole engineering-guidelines block, identical on every call of
every run. That is why `app/llm/guidelines/` had to be cut in half — it was
being re-sent on every turn of every agent, and it was hastening the shared-org
daily token cap.

The prompt is now assembled per-run from four layers, cheapest first:

    1. role prompt        the agent's job          (always)
    2. guidelines         engineering standards    (always, already trimmed)
    3. skill catalog      name + description only  (always, ~30 tok/skill)
    4. skill bodies       the expensive part       (only when triggered)

Layer 4 is what progressive disclosure buys: a skill that would cost 800 tokens
on every call costs 0 until the user's request mentions it, or the agent
deliberately calls `read_skill`.

The composed prompt is a plain string, so nothing downstream changes: it still
becomes `messages[0]` in `app.agents.base.run_tool_loop`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app import skills
from app.llm import prompts

log = logging.getLogger("asterion.context")


@dataclass(frozen=True)
class AgentContext:
    """Everything that shapes an agent's behaviour, beyond its tools."""

    agent: str
    # The request this run is about. Drives keyword-triggered skills.
    query: str = ""
    # Extra blocks the caller wants in the prompt (retrieved memories, the
    # workspace file listing, a parent agent's briefing).
    extras: tuple[str, ...] = field(default=())
    # Skip skill loading entirely. Sub-agents on a narrow job inherit their
    # briefing from the parent and don't need the catalog re-sent.
    include_skills: bool = True

    def system_prompt(self) -> str:
        parts: list[str] = [prompts.load(self.agent)]

        if self.include_skills:
            for skill in skills.always_on_for(self.agent):
                parts.append(skill.render())

            triggered = skills.activate_for(self.agent, self.query)
            if triggered:
                parts.append(skills.render_activated(triggered))

            catalog = skills.catalog_for(self.agent)
            if catalog:
                parts.append(catalog)

        parts.extend(e for e in self.extras if e and e.strip())
        return "\n\n".join(parts)

    def with_extra(self, block: str) -> AgentContext:
        return AgentContext(
            agent=self.agent,
            query=self.query,
            extras=(*self.extras, block),
            include_skills=self.include_skills,
        )


def system_prompt_for(agent: str, query: str = "", *, extras: list[str] | None = None) -> str:
    """Convenience for the common case. Equivalent to the old
    `prompts.load(agent)` when there are no skills on disk, which is what keeps
    this change safe to land: an empty `.agents/skills/` reproduces the previous
    prompt byte-for-byte."""
    return AgentContext(agent=agent, query=query, extras=tuple(extras or ())).system_prompt()


def observation_context(agent: str, paths: list[str]) -> str:
    """Path-triggered skills for files the agent just touched.

    Returned as a block to append to a tool result, not to the system prompt —
    the conventions for `*.route.ts` should arrive at the moment the agent opens
    a route file, and cost nothing on every other turn.
    """
    hits = skills.activate_for_paths(agent, paths)
    return skills.render_activated(hits) if hits else ""
