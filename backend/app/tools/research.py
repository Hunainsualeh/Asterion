"""Web research tool, backed by Groq's `groq/compound` agentic model.

`groq/compound` has built-in web search/browse/code-execution tools invoked
server-side — but it does not support combining those with our own per-agent
tool registry in the same call. So this is a standalone sub-call: the agent
calls `web_search`, we make a separate, tool-free `groq/compound` request,
and hand back the synthesized answer as a plain tool result.
"""
from __future__ import annotations

from app.llm.client import chat_completion
from app.tools.registry import ToolContext, register

COMPOUND_MODEL = "groq/compound"


@register(
    name="web_search",
    description=(
        "Search the web for current, real information to ground a decision — e.g. current best "
        "practices, whether a library/API still exists, typical pricing, current standards. "
        "Returns a synthesized answer, not raw links."
    ),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "A specific, well-formed search query."}},
        "required": ["query"],
    },
    agents=["scope", "architect"],
)
async def web_search(ctx: ToolContext, query: str) -> dict:
    response = await chat_completion(
        messages=[{"role": "user", "content": query}],
        model=COMPOUND_MODEL,
        temperature=0.2,
    )
    message = response.choices[0].message
    executed_tools = getattr(message, "executed_tools", None)
    sources: list[str] = []
    if executed_tools:
        for tool_call in executed_tools:
            search_results = getattr(tool_call, "search_results", None)
            if search_results:
                results = getattr(search_results, "results", None) or []
                sources.extend(getattr(r, "url", "") for r in results if getattr(r, "url", ""))
    return {"answer": message.content or "", "sources": sources}
