"""Artifact writer — persists agent-produced documents to disk.

Scope/architecture/ticket docs are also the graph's source of truth (returned
in node output and checkpointed), but writing them to
workspace/<project_id>/docs/ as well means a human can open the actual files,
and later agents (Developer, Reviewer) can read them back with `read_file`
instead of needing them re-injected into every prompt.
"""
from __future__ import annotations

from app.tools.registry import ToolContext, register

_FILENAMES = {
    "scope": "scope.md",
    "architecture": "architecture.md",
    "tickets": "tickets.md",
    "docs": "changelog.md",
    "final_report": "final_report.md",
}


@register(
    name="write_artifact",
    description=(
        "Save a finished document (scope, architecture, tickets, or docs) to the "
        "project's docs folder so it is durably recorded."
    ),
    parameters={
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(_FILENAMES),
                "description": "Which document this is.",
            },
            "content": {"type": "string", "description": "Full markdown content to save."},
        },
        "required": ["kind", "content"],
    },
    agents=["scope", "architect", "planner", "developer"],
)
async def write_artifact(ctx: ToolContext, kind: str, content: str) -> dict:
    filename = _FILENAMES.get(kind)
    if filename is None:
        raise ValueError(f"unknown artifact kind: {kind}")
    path = ctx.docs_dir / filename
    if kind == "docs" and path.exists():
        content = path.read_text(encoding="utf-8") + "\n\n---\n\n" + content
    path.write_text(content, encoding="utf-8")
    return {"saved": str(path)}
