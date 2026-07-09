"""What each agent sees, and when.

Three questions, three functions:

    catalog_for(agent)          which skills exist?      -> name + description
    activate_for(agent, query)  which fire right now?    -> full bodies
    body_of(agent, name)        give me that one         -> full body (read_skill tool)

The token budget is enforced here rather than trusted to the caller. Asterion
runs against free-tier caps as low as 6-8K tokens per *request*, and a skill
that silently blows the budget doesn't produce a nice error — it produces a 413
mid-agent-run, which `app/agents/base.py` can only answer by escalating to a
different model. So the catalog is capped, and activated bodies are capped.
"""
from __future__ import annotations

import logging

from app.skills.loader import load_skills
from app.skills.models import Loading, Skill

log = logging.getLogger("asterion.skills")

# ~4 chars/token, matching the estimate app/agents/base.py already trims to.
# The catalog is re-sent on every single call, so it is kept genuinely small:
# 40 skills at a one-line description each still fits.
MAX_CATALOG_CHARS = 3_000
# Activated bodies land in one turn, alongside the transcript and tool schemas.
MAX_ACTIVATED_CHARS = 6_000


def all_skills() -> dict[str, Skill]:
    return load_skills()


def skills_for(agent: str) -> list[Skill]:
    return [s for s in load_skills().values() if s.visible_to(agent)]


def get(agent: str, name: str) -> Skill | None:
    """A skill by name, but only if this agent is allowed to see it. Mirrors
    `tools.registry.dispatch`: the allowlist is re-checked at use, not just at
    listing, so a model that hallucinates a skill name it was never shown
    cannot read it."""
    skill = load_skills().get(name)
    return skill if skill and skill.visible_to(agent) else None


def body_of(agent: str, name: str) -> str | None:
    skill = get(agent, name)
    return skill.body if skill else None


def catalog_for(agent: str) -> str:
    """Stage 1 of progressive disclosure: the always-present index.

    Always-on skills are omitted — their entire body is already in the prompt,
    so listing them again is pure waste. Path-triggered skills are omitted too:
    the agent cannot usefully "decide" to load one, it fires off a file touch.
    """
    listed = [
        s
        for s in skills_for(agent)
        if s.loading in (Loading.KEYWORD, Loading.ON_DEMAND)
    ]
    if not listed:
        return ""

    lines: list[str] = []
    used = 0
    for skill in sorted(listed, key=lambda s: s.name):
        line = skill.catalog_line()
        if used + len(line) > MAX_CATALOG_CHARS:
            log.warning(
                "Skill catalog for '%s' truncated at %d chars — %d skill(s) hidden",
                agent, MAX_CATALOG_CHARS, len(listed) - len(lines),
            )
            break
        lines.append(line)
        used += len(line)

    return (
        "<AVAILABLE_SKILLS>\n"
        "Reference material you can pull in when it is relevant. Call "
        "`read_skill(name)` to read one in full before you rely on it.\n"
        + "\n".join(lines)
        + "\n</AVAILABLE_SKILLS>"
    )


def _cap(skills: list[Skill], limit: int, why: str) -> list[Skill]:
    kept: list[Skill] = []
    used = 0
    for skill in skills:
        rendered = len(skill.render())
        if used + rendered > limit:
            log.warning("Dropping skill '%s' from %s: over the %d-char budget", skill.name, why, limit)
            continue
        kept.append(skill)
        used += rendered
    return kept


def always_on_for(agent: str) -> list[Skill]:
    return [s for s in skills_for(agent) if s.loading is Loading.ALWAYS]


def activate_for(agent: str, query: str) -> list[Skill]:
    """Stage 2, the free half: keyword triggers matched against the request.

    Costs nothing when nothing matches, which is the common case — that is the
    entire argument for preferring a keyword trigger over an always-on skill.
    """
    if not query:
        return []
    hits = [s for s in skills_for(agent) if s.matches_text(query)]
    if hits:
        log.info("Skills triggered for %s: %s", agent, ", ".join(s.name for s in hits))
    return _cap(hits, MAX_ACTIVATED_CHARS, f"activation for {agent}")


def activate_for_paths(agent: str, paths: list[str]) -> list[Skill]:
    """Stage 2, the deterministic half: the agent touched a matching file.

    Fires on the *observation*, not the request — the agent reading
    `src/api/users.route.ts` learns the API conventions at the moment it
    matters, without those conventions costing anything on the other 90% of
    turns."""
    if not paths:
        return []
    hits = [s for s in skills_for(agent) if any(s.matches_path(p) for p in paths)]
    return _cap(hits, MAX_ACTIVATED_CHARS, f"path activation for {agent}")


def render_activated(skills: list[Skill]) -> str:
    return "\n\n".join(s.render() for s in skills)
