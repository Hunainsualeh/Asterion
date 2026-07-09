"""The single entry point every agent uses to talk to an LLM.

`chat_completion` looks at the model id, picks the provider that serves it
(see `app.llm.catalog.resolve`), and forwards the call. Both provider clients
return the same response shape and raise the same `app.llm.errors` types, so
callers never branch on provider.

    from app.llm.client import chat_completion
    resp = await chat_completion(messages, model="deepseek/deepseek-v4-pro")

Import `groq_client` / `deepseek_client` directly only for provider-specific
concerns (health checks, model discovery, key-pool introspection).
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.llm import deepseek_client, groq_client
from app.llm.catalog import Provider, resolve

log = logging.getLogger("asterion.llm")

# Kept in sync with groq_client's, which is the tighter of the two budgets.
DEFAULT_MAX_TOKENS = groq_client.DEFAULT_MAX_TOKENS


def default_model() -> str:
    """The model used when a caller passes none. Agents normally go through
    `app.llm.routing.chain_for(agent)` instead of relying on this."""
    return get_settings().groq_model


async def chat_completion(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
    model: str | None = None,
    temperature: float = 0.15,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
    response_format: dict[str, Any] | None = None,
    timeout_s: float | None = None,
):
    """Provider-dispatching chat completion.

    Every argument behaves identically across providers. `timeout_s` defaults
    to each provider's own ceiling (Groq is fast and gets 30s; DeepSeek's
    reasoning models are slower and get 60s) rather than one shared number that
    would be either too tight for one or too slack for the other.
    """
    resolved = model or default_model()
    provider, native_id = resolve(resolved)

    kwargs: dict[str, Any] = {
        "tools": tools,
        "tool_choice": tool_choice,
        "model": native_id,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": response_format,
    }
    if timeout_s is not None:
        kwargs["timeout_s"] = timeout_s

    if provider is Provider.DEEPSEEK:
        return await deepseek_client.chat_completion(messages, **kwargs)
    return await groq_client.chat_completion(messages, **kwargs)


async def health_check() -> dict[str, Any]:
    """Per-provider liveness. Never raises: a broken provider reports its own
    error so the caller can render a status page for the working ones."""
    report: dict[str, Any] = {}
    try:
        report["groq"] = {"ok": True, **await groq_client.health_check()}
    except Exception as exc:  # noqa: BLE001 — a health check must not 500
        report["groq"] = {"ok": False, "error": str(exc)}

    if not deepseek_client.configured():
        report["deepseek"] = {"ok": False, "error": "no API key configured"}
    else:
        try:
            report["deepseek"] = {"ok": True, **await deepseek_client.health_check()}
        except Exception as exc:  # noqa: BLE001
            report["deepseek"] = {"ok": False, "error": str(exc)}
    return report
