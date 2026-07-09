"""Local-only git tools — branch / commit / diff / merge.

Everything runs inside the project's own `repo_dir` via subprocess, and only
ever touches that local repository. No `git remote`, no `push`, no GitHub CLI
— "pull request" in this pipeline is an internal review handoff (branch +
diff parked for the Reviewer), not a real GitHub PR.
"""
from __future__ import annotations

import asyncio

from app.tools.registry import ToolContext, register

_AGENTS_WRITE = ["developer", "debugger"]
_AGENTS_READ = ["developer", "reviewer", "debugger"]


async def _run_git(ctx: ToolContext, *args: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(ctx.repo_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return {
        "returncode": proc.returncode,
        "stdout": out.decode(errors="replace"),
        "stderr": err.decode(errors="replace"),
    }


async def ensure_repo(ctx: ToolContext) -> None:
    """Idempotently init the repo + local identity + an empty initial commit."""
    git_dir = ctx.repo_dir / ".git"
    if git_dir.exists():
        return
    await _run_git(ctx, "init", "-q", "-b", "main")
    await _run_git(ctx, "config", "user.email", "agent@asterion.local")
    await _run_git(ctx, "config", "user.name", "Asterion Agent")
    (ctx.repo_dir / ".gitkeep").write_text("", encoding="utf-8")
    await _run_git(ctx, "add", "-A")
    await _run_git(ctx, "commit", "-q", "-m", "chore: initial commit", "--allow-empty")


@register(
    name="git_branch",
    description="Create (or switch to) a local git branch off main for the current ticket.",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Branch name, e.g. ticket/T-001."}},
        "required": ["name"],
    },
    agents=_AGENTS_WRITE,
)
async def git_branch(ctx: ToolContext, name: str) -> dict:
    await ensure_repo(ctx)
    await _run_git(ctx, "checkout", "-q", "main")
    result = await _run_git(ctx, "checkout", "-q", "-B", name)
    return {"branch": name, "ok": result["returncode"] == 0, "stderr": result["stderr"]}


@register(
    name="git_commit",
    description="Stage all changes and commit them on the current branch.",
    parameters={
        "type": "object",
        "properties": {"message": {"type": "string", "description": "Commit message."}},
        "required": ["message"],
    },
    agents=_AGENTS_WRITE,
)
async def git_commit(ctx: ToolContext, message: str) -> dict:
    await ensure_repo(ctx)
    await _run_git(ctx, "add", "-A")
    result = await _run_git(ctx, "commit", "-q", "-m", message)
    ok = result["returncode"] == 0 or "nothing to commit" in result["stdout"].lower()
    return {"ok": ok, "stdout": result["stdout"], "stderr": result["stderr"]}


@register(
    name="git_diff",
    description="Show the diff between the current branch and main (the 'PR' for review).",
    parameters={
        "type": "object",
        "properties": {"base": {"type": "string", "description": "Base branch/ref.", "default": "main"}},
        "required": [],
    },
    agents=_AGENTS_READ,
)
async def git_diff(ctx: ToolContext, base: str = "main") -> dict:
    await ensure_repo(ctx)
    result = await _run_git(ctx, "diff", f"{base}...HEAD")
    return {"diff": result["stdout"] or "(no changes)", "stderr": result["stderr"]}


@register(
    name="git_merge",
    description="Merge a completed ticket branch into main (only after the ticket passes manual test).",
    parameters={
        "type": "object",
        "properties": {"branch": {"type": "string", "description": "Branch to merge into main."}},
        "required": ["branch"],
    },
    agents=["debugger", "developer"],
)
async def git_merge(ctx: ToolContext, branch: str) -> dict:
    await ensure_repo(ctx)
    await _run_git(ctx, "checkout", "-q", "main")
    result = await _run_git(ctx, "merge", "-q", "--no-ff", branch, "-m", f"merge: {branch}")
    return {"ok": result["returncode"] == 0, "stdout": result["stdout"], "stderr": result["stderr"]}
