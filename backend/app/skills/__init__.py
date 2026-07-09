"""Skills: progressively-disclosed behavioural knowledge for agents.

    from app import skills
    skills.catalog_for("developer")           # cheap index, every call
    skills.activate_for("developer", query)   # bodies, only when triggered
    skills.body_of("developer", "python-testing")   # the read_skill tool

Skills live in `.agents/skills/<name>/SKILL.md`. See app/skills/models.py for
why the two-stage loading exists, and `.agents/skills/README.md` for how to
write one.
"""
from __future__ import annotations

from app.skills.loader import SkillParseError, load_skills, parse_skill
from app.skills.models import ALL_AGENTS, Loading, Skill
from app.skills.registry import (
    activate_for,
    activate_for_paths,
    all_skills,
    body_of,
    catalog_for,
    get,
    always_on_for,
    render_activated,
    skills_for,
)

__all__ = [
    "ALL_AGENTS",
    "Loading",
    "Skill",
    "SkillParseError",
    "activate_for",
    "activate_for_paths",
    "all_skills",
    "always_on_for",
    "body_of",
    "catalog_for",
    "get",
    "load_skills",
    "parse_skill",
    "render_activated",
    "skills_for",
]
