"""Distilled engineering standards, injected into code-touching agents.

The three reference PDFs the owner supplied (Software Engineering; Software
Testing & Quality Assurance; a UI/UX Design Guide) are academic/reference texts
far too large for this project's free-tier Groq token budget — transcripts are
already trimmed at 22K chars in `app.agents.base`. Each is distilled here into a
compact standards block, and `for_agent` composes the right block(s) per role so
the SDLC-lane agents (via `app.llm.prompts.load`) and the DAG task-lane
executors (via explicit calls in `app.dag.workflows`) share one raised bar.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent

# Role key -> ordered guideline docs (filenames without .md). Keys cover both
# lanes: SDLC keys match app/llm/prompts/*.md names; task-lane keys match the
# executor/routing roles used in app/dag/workflows.py.
_AGENT_DOCS: dict[str, tuple[str, ...]] = {
    "developer": ("software_engineering", "ui_ux"),
    "architect": ("software_engineering", "ui_ux"),
    "reviewer": ("software_engineering", "quality_assurance"),
    "debugger": ("quality_assurance", "software_engineering"),
    "planner": ("software_engineering",),
    "coder": ("software_engineering", "ui_ux"),
    "designer": ("ui_ux",),
}

# Safety cap so a composed standards block can never crowd out the tiny per-call
# TPM budget (base.py trims whole transcripts at 22K chars).
_MAX_BLOCK_CHARS = 4000


@lru_cache
def _doc(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


@lru_cache
def for_agent(agent: str) -> str:
    """The composed standards block for a role, or '' if the role has none."""
    docs = _AGENT_DOCS.get(agent)
    if not docs:
        return ""
    block = "\n\n".join(_doc(name) for name in docs)
    return block[:_MAX_BLOCK_CHARS]
