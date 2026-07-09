"""Shell tool — run lint/build/test commands inside the project's sandbox.

Always executes with cwd pinned to the project's repo_dir, so a command can
only affect files under that project's own workspace. A hard timeout keeps a
runaway command (dev server, infinite loop) from hanging the agent loop.
"""
from __future__ import annotations

import asyncio

from app.tools.registry import ToolContext, register

MAX_OUTPUT = 20_000
DEFAULT_TIMEOUT = 60


@register(
    name="run_command",
    description=(
        "Run a shell command (lint, build, test, install deps) inside the project's "
        "workspace. Runs with a timeout; long-running/server commands will be killed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "timeout": {"type": "integer", "description": "Seconds before the command is killed.", "default": DEFAULT_TIMEOUT},
        },
        "required": ["command"],
    },
    agents=["developer", "reviewer", "debugger"],
)
async def run_command(ctx: ToolContext, command: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    from app.tools.git_tools import ensure_repo

    await ensure_repo(ctx)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(ctx.repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"timed_out": True, "returncode": None, "stdout": "", "stderr": f"killed after {timeout}s"}
    except FileNotFoundError as exc:
        return {"timed_out": False, "returncode": -1, "stdout": "", "stderr": str(exc)}

    return {
        "timed_out": False,
        "returncode": proc.returncode,
        "stdout": out.decode(errors="replace")[:MAX_OUTPUT],
        "stderr": err.decode(errors="replace")[:MAX_OUTPUT],
    }
