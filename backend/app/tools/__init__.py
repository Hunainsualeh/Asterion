"""Importing this package registers every tool (decorators run on import)."""
from __future__ import annotations

from app.tools import (  # noqa: F401
    artifacts,
    ask_human,
    code_analysis,
    fs_tools,
    git_tools,
    knowledge_tools,
    memory_tools,
    research,
    shell_tools,
    submissions,
)
from app.tools.registry import ToolContext, dispatch, groq_tools_for  # noqa: F401
