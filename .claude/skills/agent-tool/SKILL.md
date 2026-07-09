---
name: agent-tool
description: Add, change, or debug a tool that Asterion's agents can call — anything under backend/app/tools/, the per-agent allowlist, terminal tools, or an agent's tool-calling loop. Use when an agent needs a new capability, a tool errors mid-run, or a tool call never fires.
---

# Agent tools

A tool is an async function an LLM agent can invoke by name during its
tool-calling loop. Registration is the **security boundary**: `dispatch()`
re-checks the caller's allowlist on every call, so a compromised or confused
loop cannot reach past its agent's declared capabilities. Never bypass it.

## Adding one

Create or extend a module in `app/tools/`, then register the handler:

```python
from app.tools.registry import ToolContext, register

@register(
    name="read_file",
    description="Read a UTF-8 text file from the project workspace.",  # the model reads this
    parameters={                                                       # JSON Schema
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path relative to the repo root."}},
        "required": ["path"],
    },
    agents=["developer", "reviewer", "debugger"],                      # the allowlist
)
async def read_file(ctx: ToolContext, path: str) -> dict:
    return {"content": (ctx.repo_dir / path).read_text(encoding="utf-8")}
```

Then add the module to `app/tools/__init__.py` — importing that package is what
runs the decorators. **A tool in a module nobody imports simply does not
exist**, with no error to tell you so.

Details that bite:

- **The handler's first parameter is always `ctx`.** `dispatch()` filters the
  model's arguments against the handler signature (`inspect.signature`), so an
  argument the model hallucinates is dropped rather than raising `TypeError` —
  but a parameter you forgot to declare in `parameters` will never be *sent*.
- **`ctx` is the sandbox.** Use `ctx.repo_dir` / `ctx.workspace_dir`, never a
  raw path. They're per-project and created on first use.
- **Return something JSON-serialisable.** The loop does
  `json.dumps(result, default=str)` and feeds it back as the tool message.
- **Names are globally unique.** `register` raises on a duplicate at import
  time, which surfaces as the whole app failing to start.
- **`description` is a prompt.** It's the only thing the model sees when
  deciding whether to call your tool. Write it for the model, not the reader.

## Terminal tools

Each agent declares `terminal_tools` — calling one *ends* the loop and its
parsed arguments become the stage's structured result. A tool that is meant to
finish an agent's turn must be in that set (`agents/<agent>.py::TERMINAL_TOOLS`),
otherwise the agent calls it, keeps looping, and eventually raises
`ToolLoopExhausted` after `MAX_ITERATIONS`.

## When a tool errors

Handler exceptions are caught in `agents/base.py::run_tool_loop`, logged, and
returned to the model as `{"error": "..."}` instead of crashing the run. This is
deliberate: the model usually recovers by fixing its arguments. Consequences:

- A tool that fails *silently and returns success* is far worse than one that
  raises. Raise.
- Error strings are read by an LLM. `"path 'src/x.py' does not exist; call
  list_files first"` beats `"ENOENT"`.
- Every call is recorded (`record_tool_call`) and published as a `tool_call`
  event, visible in the UI's Activity drawer with latency and an args preview.

## When a tool never fires

Work down this list:

1. Is the agent in the tool's `agents=[...]` allowlist? `tool_names_for(agent)`
   returns exactly what that agent can see.
2. Is the module imported in `app/tools/__init__.py`?
3. Is the model emitting a malformed call? `MalformedToolCall` is transient
   sampling noise — `base.py` resamples up to `MAX_MALFORMED_RETRIES`, then
   escalates to the next model. `llama-3.1-8b-instant` does this often enough
   that it's a real capability limit, not noise; the high-volume agents sit on
   it and escalate.
4. Is the tool list too long? The transcript is trimmed to
   `CONTEXT_CHAR_BUDGET`, but the tool *schemas* are re-sent every turn. Many
   verbose descriptions crowd out the free tier's 6–8K TPM request ceiling.

## Verify

```bash
cd backend
.venv/Scripts/python.exe -c "
import app.tools
from app.tools.registry import all_tools, tool_names_for
print('registered:', sorted(all_tools()))
print('developer sees:', sorted(tool_names_for('developer')))"
```

Then dispatch it directly, allowlist and all:

```bash
.venv/Scripts/python.exe -c "
import asyncio, app.tools
from app.tools.registry import ToolContext, dispatch
ctx = ToolContext(project_id='scratch', agent='developer')
print(asyncio.run(dispatch(ctx, 'read_file', {'path': 'README.md'})))"
```
