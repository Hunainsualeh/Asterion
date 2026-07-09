"""Sandboxed filesystem tools.

Every path is resolved against the calling project's `repo_dir` and verified
to stay inside it — an agent can never read or write outside its own
project's workspace, regardless of `..` segments or absolute-looking paths.
"""
from __future__ import annotations

from pathlib import Path

from app.tools.registry import ToolContext, register

MAX_READ_BYTES = 200_000


class SandboxViolation(RuntimeError):
    pass


def _resolve(ctx: ToolContext, rel_path: str) -> Path:
    root = ctx.repo_dir.resolve()
    candidate = (root / rel_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise SandboxViolation(f"path escapes the project workspace: {rel_path}")
    return candidate


@register(
    name="read_file",
    description="Read a text file's contents, relative to the project's repo root.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Relative file path."}},
        "required": ["path"],
    },
    agents=["architect", "developer", "reviewer", "debugger"],
)
async def read_file(ctx: ToolContext, path: str) -> dict:
    target = _resolve(ctx, path)
    if not target.exists() or not target.is_file():
        return {"error": f"no such file: {path}"}
    data = target.read_text(encoding="utf-8", errors="replace")
    truncated = len(data) > MAX_READ_BYTES
    return {"path": path, "content": data[:MAX_READ_BYTES], "truncated": truncated}


@register(
    name="write_file",
    description="Create or overwrite a text file, relative to the project's repo root.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative file path."},
            "content": {"type": "string", "description": "Full file contents."},
        },
        "required": ["path", "content"],
    },
    agents=["developer", "debugger"],
)
async def write_file(ctx: ToolContext, path: str, content: str) -> dict:
    target = _resolve(ctx, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"saved": path, "bytes": len(content.encode("utf-8"))}


@register(
    name="list_dir",
    description="List files and folders under a directory in the project's repo root.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Relative dir path, '.' for root."}},
        "required": [],
    },
    agents=["architect", "developer", "reviewer", "debugger"],
)
async def list_dir(ctx: ToolContext, path: str = ".") -> dict:
    target = _resolve(ctx, path)
    if not target.exists():
        return {"error": f"no such directory: {path}", "entries": []}
    entries = sorted(
        f"{p.name}/" if p.is_dir() else p.name
        for p in target.iterdir()
        if p.name != ".git"
    )
    return {"path": path, "entries": entries}
