"""Loads per-agent system prompts from the sibling .md files, composed with the
shared engineering-standards block for code-touching roles."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.llm import guidelines

_DIR = Path(__file__).resolve().parent


@lru_cache
def load(agent: str) -> str:
    base = (_DIR / f"{agent}.md").read_text(encoding="utf-8")
    standards = guidelines.for_agent(agent)
    return f"{base}\n\n{standards}" if standards else base
