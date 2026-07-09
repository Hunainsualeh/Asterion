"""The Skill model and its trigger types.

A skill is a unit of *behavioural* knowledge — a convention, a checklist, a
recipe — that an agent should follow when a situation arises. It is not a tool
(tools are code the agent calls; skills are instructions the agent reads).

The whole point is **progressive disclosure**. Asterion's LLM budget is the
binding constraint on this project: free-tier Groq caps a single request at
6-8K tokens on the smaller models, and `app/llm/guidelines/` already had to be
cut in half because injecting whole documents into every call was hastening the
shared-org daily token cap. So a skill exposes itself in two stages:

    stage 1  name + description   (~30 tokens)  -> always in the system prompt
    stage 2  the full body        (~500-3000)   -> loaded only when needed

Stage 2 fires in one of four ways, cheapest first:

    ALWAYS    no trigger declared; body joins the system prompt (use sparingly)
    KEYWORD   a word in the user's request matches; body is injected up front
    PATH      the agent touched a file matching a glob; body is injected then
    ON DEMAND the agent calls the `read_skill` tool after reading the catalog

`ALWAYS` is the legacy, expensive shape. Prefer a keyword trigger, and prefer
on-demand over that.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# A skill visible to every agent. Matches the allowlist convention in
# app/tools/registry.py, where a tool names the agents that may call it.
ALL_AGENTS = "*"


class Loading(str, Enum):
    """When this skill's body reaches the model."""

    ALWAYS = "always"        # in the system prompt, every call
    KEYWORD = "keyword"      # injected when the request mentions a trigger word
    PATH = "path"            # injected when a touched file matches a glob
    ON_DEMAND = "on_demand"  # only when the agent calls read_skill(name)


def _normalize(text: str) -> str:
    """Lowercase, punctuation-stripped, single-spaced — so `"Dockerfile."`
    matches the trigger `dockerfile`."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s/.-]", " ", text.lower())).strip()


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    loading: Loading
    # Keyword triggers, already normalized. A single-word trigger must match a
    # whole word; a multi-word trigger matches as a phrase.
    triggers: tuple[str, ...] = ()
    # Glob patterns, matched against paths the agent reads or writes.
    paths: tuple[str, ...] = ()
    # Which agents may see this skill at all. ("*",) = every agent.
    agents: tuple[str, ...] = (ALL_AGENTS,)
    source: Path | None = field(default=None, compare=False)

    # ---------------------------------------------------------------- visibility
    def visible_to(self, agent: str) -> bool:
        return ALL_AGENTS in self.agents or agent in self.agents

    # ---------------------------------------------------------------- triggers
    def matches_text(self, text: str) -> bool:
        if self.loading is not Loading.KEYWORD or not self.triggers:
            return False
        haystack = _normalize(text)
        for trigger in self.triggers:
            if " " in trigger:
                if trigger in haystack:
                    return True
            # Single words must not fire on substrings: the trigger `api`
            # should not match `rapidly`. \b would miss `c++`, so pad instead.
            elif f" {trigger} " in f" {haystack} ":
                return True
        return False

    def matches_path(self, path: str) -> bool:
        if self.loading is not Loading.PATH or not self.paths:
            return False
        candidate = path.replace("\\", "/").lstrip("./")
        return any(
            fnmatch.fnmatch(candidate, pattern) or fnmatch.fnmatch(f"/{candidate}", pattern)
            for pattern in self.paths
        )

    # ---------------------------------------------------------------- rendering
    def catalog_line(self) -> str:
        """Stage 1. What every agent sees, for every skill, on every call."""
        return f"- {self.name}: {self.description}"

    def render(self) -> str:
        """Stage 2. Wrapped so the model can tell injected context apart from
        the user's own words — the same `<EXTRA_INFO>` convention OpenHands
        uses, which measurably reduces the model treating context as an
        instruction it must act on."""
        return f'<EXTRA_INFO source="skill:{self.name}">\n{self.body.strip()}\n</EXTRA_INFO>'
