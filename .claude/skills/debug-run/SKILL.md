---
name: debug-run
description: Diagnose a failed, stuck, or wrong-looking Asterion run — a pipeline that stalls at a gate, an agent that loops without finishing, a project that 404s, a run that used an unexpected model, or a confusing error in the chat. Use when triaging runtime behaviour rather than editing a specific module.
---

# Debugging a run

## First, establish which lane you're in

`api/routes/chat.py` classifies every message and sends it down one of two very
different code paths. Debugging the wrong one wastes the session.

- **Project lane** (`orchestration/`) — LangGraph state machine, human approval
  gates, the six agents. Entered when `intent.kind == "software_project"`.
- **Task lane** (`dag/`) — a dynamically expanded DAG of `exec_*` executors in
  `dag/workflows.py`. Everything else: questions, analysis, research, one-off
  code, reminders, app control.

`GET /api/projects/{pid}` reports the project's `lane` and `intent`. Start there,
not in the code.

## Rule out the environment before the code

Three things masquerade as bugs in this project:

**A stale backend.** Behaviour that contradicts the source usually means an old
process is still serving. `netstat -ano | grep :8000`, kill it, restart with
`.venv/Scripts/python.exe dev.py`. Never theorise against an unverified server.

**fakeredis.** If native Redis wasn't running at startup, the backend logs a
loud warning and falls back to in-process fakeredis. State is neither durable
nor cross-process, so a project that vanishes after a restart, or a 404 from a
second process, is expected — not a bug. `GET /health` reports which backend is
live.

**The reloader eating a run.** If agents "stop spawning" mid-run, someone
started uvicorn with plain `--reload`: it watches `workspace/`, so the agents'
own output restarts the server. Use `dev.py`.

## Read the run's own record

Every stage publishes SSE events (`orchestration/events.py`). Kinds:
`agent_started`, `agent_message`, `running`, `tool_call`, `result`, `gate`,
`awaiting_input`, `ui_action`, `done`, `error`, `cancelled`.

The **Activity drawer** in the UI renders the technical ones — including every
tool call with its latency, ok/error status, and an args preview. That is
usually faster than reading logs, because it shows what the *model* decided, not
just what the code did.

Per-project metrics (token counts, per-agent latency, estimated cost, recent
calls) come from `observability/metrics.py::get_project_metrics`.

## Symptom → cause

**Stuck at a gate.** The pipeline is *supposed* to block. `GET
/api/projects/{pid}` shows the pending interrupt. A gate only clears via
`/approvals` or a `/chat` answer, and a re-fired gate gets a fresh
`interrupt_id` — answering with a stale one 409s deliberately, so a double
submit can't skip a gate.

**`ToolLoopExhausted`.** The agent ran `MAX_ITERATIONS` turns without calling a
terminal tool. Either the tool it needs isn't in that agent's `TERMINAL_TOOLS`,
or the model keeps calling non-terminal tools. Check the Activity drawer: a loop
that calls `list_files` eleven times is a prompt problem, not a plumbing one.

**Agent replies with prose instead of calling a tool.** Handled in-loop:
`base.py` appends `NUDGE_NO_TOOL_CALL` and re-asks. Persistent prose from
`llama-3.1-8b-instant` is a capability limit — it escalates.

**`413` / `RequestTooLarge`.** The transcript exceeded the model's per-request
TPM ceiling (as low as 6–8K on the free tier). `_trim_messages` compacts old
tool outputs first, then long old messages, never the system prompt or the last
`TRIM_KEEP_LAST` exchanges. If it still 413s, the *tool schemas* are the bulk —
they're re-sent every turn.

**The wrong model answered.** Escalation is logged: grep the backend log for
`escalating`. Then check, in order: is a Settings › Models override active
(`selection.current()`)? Does the call site pin an explicit `model=` (which
beats the override on purpose)? What does `chain_for(agent)` resolve to?

**DeepSeek "doesn't work" but the key is right.** DeepSeek is prepaid. A
zero-balance key authenticates, answers `GET /models`, and then returns HTTP 402
on *every* completion. `GET /api/models` surfaces the balance; the run silently
degrades to Groq by design. See `.claude/skills/llm-provider/SKILL.md`.

**A confusing message in the chat.** User-facing errors are produced by
`orchestration/stages.py::describe_error`, which maps an exception to a
title/explanation/suggestion plus a `retryable` flag and a short reference id.
The full exception is logged server-side by `runner.py` first — search the log
for the reference id shown in the UI. If a new exception type reaches the user
as a generic "something went wrong", add a branch there rather than widening an
existing one.

## Reproduce without the UI

Route handlers are plain coroutines; drive them directly.

```bash
cd backend
.venv/Scripts/python.exe -c "
import asyncio
from app.orchestration.intent import classify
print(asyncio.run(classify('build me a todo app with reminders')))"
```

`scripts/` holds standing harnesses: `smoke_test.py`, `test_pipeline.py`,
`test_real_agents.py`, `test_debugger_isolated.py`, `test_dev_loop.py`. They hit
live LLMs and spend quota — read one before running it.
