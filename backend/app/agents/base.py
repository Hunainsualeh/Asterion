"""The shared Groq tool-calling loop every real agent runs on.

Each stage is: send the system prompt + context, let the model call tools
(mutating side effects go through the registry's allowlist-enforced
`dispatch`), and keep going until it calls one of that stage's declared
`terminal_tools` — at which point the loop stops and hands back that tool's
parsed arguments as the stage's structured result.

Tool-calling is occasionally flaky in two ways: the model replies with plain
text instead of a tool call, or it emits an unparseable inline `<function=...>`
string that the provider itself rejects with a 400. Both are handled in-loop by
showing the model what it just did wrong and asking it to correct itself on the
next turn, rather than crashing the whole agent run.

Nothing here knows which provider answered. `app.llm.client` dispatches on the
model id, and every provider raises the same `app.llm.errors` types — so the
escalation ladder below is written once, in terms of *what went wrong*, not
*who said so*.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

import app.tools  # noqa: F401 - import triggers every tool's @register decorator
from app.llm.client import chat_completion
from app.llm.errors import (
    MalformedToolCall,
    OverCapacity,
    ProviderUnavailable,
    RateLimited,
    RequestTooLarge,
)
from app.llm.routing import chain_for
from app.observability import record_llm_call, record_tool_call
from app.tools.registry import ToolContext, dispatch, groq_tools_for

log = logging.getLogger("asterion.agents")

MAX_ITERATIONS = 10
# Resample budget per model before escalating to the next model in the
# agent's routing chain. Malformed tool calls are sampling noise on the
# bigger models but a real capability limit on llama-3.1-8b — so exhausting
# this budget escalates instead of failing the run.
MAX_MALFORMED_RETRIES = 3
MAX_CAPACITY_RETRIES = 3
NUDGE_NO_TOOL_CALL = "You must call one of the available tools to respond — plain text replies aren't accepted here."

# The doc's "context accumulation" failure mode, enforced at the loop level:
# free-tier Groq TPM caps are as low as 6-8K tokens/request (llama-3.1-8b,
# gpt-oss-20b), so the transcript must stay under that or long tool loops
# start 413ing. ~4 chars/token keeps a safety margin below the tightest cap.
CONTEXT_CHAR_BUDGET = 22000
TRIM_KEEP_LAST = 6  # never touch the most recent exchanges


def _trim_messages(messages: list[dict]) -> None:
    """Compact oldest tool outputs (then long older messages) in place once
    the transcript outgrows the budget. The system prompt and the last few
    messages always survive untouched; tool messages stay present (their
    tool_call_ids must keep resolving) but lose their bulk."""

    def total() -> int:
        return sum(len(str(m.get("content") or "")) for m in messages)

    if total() <= CONTEXT_CHAR_BUDGET:
        return
    for m in messages[1:-TRIM_KEEP_LAST]:
        if m.get("role") == "tool" and len(m.get("content") or "") > 300:
            m["content"] = str(m["content"])[:300] + "...[trimmed: full output was shown earlier in this session]"
            if total() <= CONTEXT_CHAR_BUDGET:
                return
    for m in messages[1:-TRIM_KEEP_LAST]:
        content = m.get("content")
        if isinstance(content, str) and len(content) > 1200:
            m["content"] = content[:1200] + "...[trimmed]"
            if total() <= CONTEXT_CHAR_BUDGET:
                return


# Failures that no amount of retrying *this* model will fix, because they are
# properties of the (model, key, request) triple rather than of one sampled
# response: the key pool is out of quota, the provider refuses the key at all
# (DeepSeek 402 on an unpaid account), or the transcript exceeds this model's
# per-request token ceiling. Every one of them is answered by moving to a model
# with a different quota, a different provider, or a bigger context window.
_ESCALATE_IMMEDIATELY = (RateLimited, ProviderUnavailable, RequestTooLarge)


async def _complete_with_retry(messages: list[dict], tools: list[dict], model: str | None, agent: str):
    """One completion attempt, walked down the agent's routing chain.

    The chain comes from litellm_config.yaml, with the user's selected model
    (if any) at its head — see app.llm.routing. An explicit `model` argument
    overrides the primary but keeps the configured fallbacks. Per model:
    malformed tool calls are resampled up to MAX_MALFORMED_RETRIES, transient
    5xx get a short backoff, and anything in `_ESCALATE_IMMEDIATELY` moves on
    at once — only the last model in the chain is allowed to raise.
    """
    configured = chain_for(agent)
    chain = configured if model is None else [model, *[m for m in configured if m != model]]

    for i, current_model in enumerate(chain):
        is_last = i == len(chain) - 1
        malformed = 0
        capacity = 0
        while True:
            try:
                return await chat_completion(messages, tools=tools, tool_choice="auto", model=current_model)
            except MalformedToolCall:
                # Transient sampling noise. Resample the same request — feeding
                # the garbled text back into the transcript tends to reinforce it.
                malformed += 1
                if malformed >= MAX_MALFORMED_RETRIES:
                    if is_last:
                        raise
                    log.warning("%s: %s keeps emitting malformed tool calls, escalating to %s", agent, current_model, chain[i + 1])
                    break
                log.warning("%s: malformed tool call from %s (attempt %d), resampling", agent, current_model, malformed)
                await asyncio.sleep(0.5 * malformed)
            except _ESCALATE_IMMEDIATELY as exc:
                if is_last:
                    raise
                log.warning(
                    "%s: %s unusable (%s), escalating to %s",
                    agent, current_model, type(exc).__name__, chain[i + 1],
                )
                break
            except OverCapacity:
                capacity += 1
                if capacity >= MAX_CAPACITY_RETRIES:
                    if is_last:
                        raise
                    log.warning("%s: %s still over capacity, escalating to %s", agent, current_model, chain[i + 1])
                    break
                log.warning("%s: %s over capacity (attempt %d), retrying", agent, current_model, capacity)
                await asyncio.sleep(2.0 * capacity)
    raise AssertionError("unreachable")


@dataclass
class LoopResult:
    tool: str
    args: dict
    messages: list[dict]
    iterations: int


class ToolLoopExhausted(RuntimeError):
    pass


async def run_tool_loop(
    ctx: ToolContext,
    system_prompt: str,
    user_messages: list[dict],
    terminal_tools: set[str],
    *,
    model: str | None = None,
    max_iterations: int = MAX_ITERATIONS,
) -> LoopResult:
    messages: list[dict] = [{"role": "system", "content": system_prompt}, *user_messages]
    tools = groq_tools_for(ctx.agent)

    for iteration in range(1, max_iterations + 1):
        _trim_messages(messages)
        llm_start = time.monotonic()
        response = await _complete_with_retry(messages, tools, model, ctx.agent)
        usage = getattr(response, "usage", None)
        await record_llm_call(
            ctx.project_id,
            ctx.agent,
            getattr(response, "model", model or ""),
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=int((time.monotonic() - llm_start) * 1000),
        )
        message = response.choices[0].message
        tool_calls = message.tool_calls or []

        if not tool_calls:
            messages.append({"role": "assistant", "content": message.content or ""})
            messages.append({"role": "user", "content": NUDGE_NO_TOOL_CALL})
            continue

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if name in terminal_tools:
                return LoopResult(tool=name, args=args, messages=messages, iterations=iteration)

            tool_start = time.monotonic()
            ok = True
            try:
                result = await dispatch(ctx, name, args)
            except Exception as exc:  # noqa: BLE001 - surface tool errors back to the model, don't crash the loop
                log.warning("%s: tool %s failed: %s", ctx.agent, name, exc)
                result = {"error": str(exc)}
                ok = False
            tool_ms = int((time.monotonic() - tool_start) * 1000)
            await record_tool_call(ctx.project_id, ctx.agent, name, tool_ms, ok)
            try:
                # Technical-log event (chat=false): powers the tool-call trace
                # in the Activity drawer without touching the chat thread.
                from app.orchestration.events import publish_event

                await publish_event(
                    ctx.project_id, "tool_call", ctx.agent,
                    f"{name} ({'ok' if ok else 'error'}, {tool_ms}ms)",
                    {"tool": name, "ok": ok, "latency_ms": tool_ms,
                     "args_preview": json.dumps(args, default=str)[:400]},
                )
            except Exception:  # noqa: BLE001 — observability must never break the loop
                log.debug("tool_call event publish failed", exc_info=True)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                }
            )

    raise ToolLoopExhausted(f"{ctx.agent}: no terminal tool call after {max_iterations} iterations")
