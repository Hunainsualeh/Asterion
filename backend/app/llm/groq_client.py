"""Thin async wrapper around the Groq chat-completions API.

Supports tool-calling (function calling) and plain completions. Backed by a
pool of Groq API keys (free/on_demand tier limits are per-key and tight) so a
rate-limited key doesn't stall an agent run — `chat_completion` transparently
rotates to the next available key for the requested model, and only raises once
every key is on cooldown for that model (at which point the caller,
`app.agents.base`, can fall back to a different model).

Nothing above `app.llm` calls this module directly: agents go through
`app.llm.client.chat_completion`, which picks a provider from the model id.
Every `groq.*` exception is translated into `app.llm.errors` here, at the
boundary, so the retry logic upstairs is provider-agnostic.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import groq
from groq import AsyncGroq

from app.config import get_settings
from app.llm.errors import (
    KeyPoolExhausted,
    LLMBadRequest,
    LLMConnectionError,
    LLMError,
    MalformedToolCall,
    OverCapacity,
    ProviderUnavailable,
    RateLimited,
    RequestTooLarge,
)

log = logging.getLogger("asterion.groq")

DEFAULT_COOLDOWN_S = 30.0
# No single Groq call is allowed to hang indefinitely — cheaper/faster to
# rotate to the next key/model than to sit waiting on one slow request. Groq
# is normally sub-few-seconds; 30s is already generous headroom.
REQUEST_TIMEOUT_S = 30.0
# Every real agent turn is a tool-calling step, not a long-form essay — an
# uncapped completion lets a reasoning model wander for thousands of hidden
# tokens (slow *and* expensive on a tight free-tier TPM budget). Generous
# enough for a full single-file `write_file` tool-call payload.
DEFAULT_MAX_TOKENS = 4096
_RETRY_AFTER_RE = re.compile(r"try again in (?:(?P<minutes>[\d.]+)m)?(?:(?P<seconds>[\d.]+)s)?")

PROVIDER = "groq"


@dataclass
class _KeySlot:
    key: str
    client: AsyncGroq
    cooldown_until: dict[str, float] = field(default_factory=dict)  # model -> monotonic() deadline

    def available_for(self, model: str) -> bool:
        return time.monotonic() >= self.cooldown_until.get(model, 0.0)


_slots: list[_KeySlot] | None = None
_next_index = 0


def _mask(key: str) -> str:
    return f"{key[:8]}...{key[-4:]}" if len(key) > 14 else "***"


def _get_slots() -> list[_KeySlot]:
    global _slots
    if _slots is None:
        keys = get_settings().groq_api_keys
        if not keys:
            raise ProviderUnavailable(
                "No Groq API key configured. Set Asterion_Secret_key (or GROQ_API_KEY) "
                "in the project .env file.",
                provider=PROVIDER,
            )
        _slots = [_KeySlot(key=k, client=AsyncGroq(api_key=k)) for k in keys]
        log.info("Groq key pool: %d key(s) loaded", len(_slots))
    return _slots


def key_pool_size() -> int:
    return len(_get_slots())


def _pick_slot(model: str) -> _KeySlot | None:
    """Round-robin among slots not currently in cooldown for this model."""
    global _next_index
    slots = _get_slots()
    for offset in range(len(slots)):
        idx = (_next_index + offset) % len(slots)
        slot = slots[idx]
        if slot.available_for(model):
            _next_index = (idx + 1) % len(slots)
            return slot
    return None


def _retry_after_seconds(exc: groq.APIStatusError) -> float:
    """Prefer the Retry-After header; fall back to parsing Groq's 'try again
    in Xm Ys' message; fall back to a fixed default if neither is present."""
    response = getattr(exc, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    if header:
        try:
            return float(header)
        except ValueError:
            pass

    body = exc.body if isinstance(exc.body, dict) else {}
    message = body.get("error", {}).get("message", "")
    match = _RETRY_AFTER_RE.search(message)
    if match and (match.group("minutes") or match.group("seconds")):
        minutes = float(match.group("minutes") or 0)
        seconds = float(match.group("seconds") or 0)
        return minutes * 60 + seconds
    return DEFAULT_COOLDOWN_S


def _error_body(exc: groq.APIStatusError) -> dict[str, Any]:
    body = exc.body if isinstance(exc.body, dict) else {}
    return body.get("error", {}) if isinstance(body.get("error"), dict) else {}


def _is_key_exhausted(exc: Exception) -> bool:
    """True for errors that mean *this key* is out of quota (rotating keys
    helps), as opposed to the model being globally overloaded or the request
    itself being too large (rotating keys doesn't help; both are handled by
    model fallback in app.agents.base)."""
    if isinstance(exc, groq.RateLimitError):
        return True
    if isinstance(exc, groq.APIStatusError) and exc.status_code == 413:
        error = _error_body(exc)
        if "reduce your message size" in error.get("message", ""):
            return False  # request > model's TPM ceiling — a different key hits the same wall
        return error.get("code") == "rate_limit_exceeded"
    return False


def _translate(exc: Exception, model: str) -> Exception:
    """Groq SDK exception -> `app.llm.errors`. Returned (not raised) so callers
    can `raise _translate(...) from exc` and keep the original traceback."""
    common = {"provider": PROVIDER, "model": model}

    if isinstance(exc, groq.APIConnectionError):
        return LLMConnectionError(str(exc), **common)
    if isinstance(exc, groq.RateLimitError):
        return RateLimited(str(exc), retry_after=_retry_after_seconds(exc), status_code=429, **common)
    if isinstance(exc, groq.AuthenticationError) or isinstance(exc, groq.PermissionDeniedError):
        return ProviderUnavailable(
            f"Groq rejected the API key: {exc}", status_code=exc.status_code, **common
        )
    if isinstance(exc, groq.InternalServerError):
        return OverCapacity(str(exc), status_code=exc.status_code, **common)
    if isinstance(exc, groq.BadRequestError):
        error = _error_body(exc)
        if error.get("code") == "tool_use_failed":
            return MalformedToolCall(error.get("message") or str(exc), status_code=400, **common)
        return LLMBadRequest(error.get("message") or str(exc), status_code=400, **common)
    if isinstance(exc, groq.APIStatusError):
        error = _error_body(exc)
        if exc.status_code == 413:
            if error.get("code") == "rate_limit_exceeded":
                return RateLimited(error.get("message") or str(exc), status_code=413, **common)
            return RequestTooLarge(error.get("message") or str(exc), status_code=413, **common)
        if exc.status_code >= 500:
            return OverCapacity(str(exc), status_code=exc.status_code, **common)
        return LLMError(error.get("message") or str(exc), status_code=exc.status_code, **common)
    return exc


async def chat_completion(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
    model: str | None = None,
    temperature: float = 0.15,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
    response_format: dict[str, Any] | None = None,
    timeout_s: float = REQUEST_TIMEOUT_S,
):
    """Chat completion with automatic key rotation on quota exhaustion.

    Tries every key in the pool that isn't currently on cooldown for `model`.
    Raises the last error once all keys are exhausted for this model.

    Default temperature is low (0.15): every call in this codebase is a
    tool-calling turn, and a lower temperature measurably reduces malformed
    tool-call retries (see app/agents/base.py) — each retry re-sends the full
    message history, so fewer retries also means meaningfully less quota
    burned per stage, on top of more predictable agent behavior.
    """
    settings = get_settings()
    resolved_model = model or settings.groq_model
    kwargs: dict[str, Any] = {"model": resolved_model, "messages": messages, "temperature": temperature}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = response_format
    if "gpt-oss" in resolved_model:
        # Reasoning-model hidden "thinking" tokens are the single biggest
        # source of both slow turns and burned TPM budget in a tool-calling
        # loop that only ever needs a short structured answer per step.
        kwargs["reasoning_effort"] = "low"

    slots_tried = 0
    last_exc: Exception | None = None
    timed_out = False
    total_slots = len(_get_slots())

    while slots_tried < total_slots:
        slot = _pick_slot(resolved_model)
        if slot is None:
            break  # every key is on cooldown for this model
        slots_tried += 1
        try:
            return await asyncio.wait_for(
                slot.client.chat.completions.create(**kwargs), timeout=timeout_s
            )
        except TimeoutError as exc:
            # A hung/slow request is treated like a rate-limited key: cool it
            # down and rotate, rather than blocking the whole agent run on it.
            last_exc = exc
            timed_out = True
            slot.cooldown_until[resolved_model] = time.monotonic() + DEFAULT_COOLDOWN_S
            log.warning(
                "Groq key %s timed out for %s after %.0fs; rotating to next key",
                _mask(slot.key),
                resolved_model,
                timeout_s,
            )
        except groq.APIStatusError as exc:
            last_exc = exc
            if not _is_key_exhausted(exc):
                # A wall every key hits identically (bad request, 5xx, oversized
                # context, dead key). Escalating the *model* is the only way out.
                raise _translate(exc, resolved_model) from exc
            cooldown = _retry_after_seconds(exc)
            slot.cooldown_until[resolved_model] = time.monotonic() + cooldown
            log.warning(
                "Groq key %s exhausted for %s (%.0fs cooldown); rotating to next key",
                _mask(slot.key),
                resolved_model,
                cooldown,
            )
        except groq.APIConnectionError as exc:
            raise _translate(exc, resolved_model) from exc

    # Reaching here means every key we tried either timed out or was out of
    # quota — every other failure raised from inside the loop. Both are the
    # same thing to the caller: this model is unusable right now, escalate to
    # the next one in the chain.
    reason = "timed out" if timed_out else "is on cooldown"
    raise KeyPoolExhausted(
        f"every Groq key {reason} for {resolved_model}", provider=PROVIDER, model=resolved_model
    ) from last_exc


async def health_check() -> dict[str, Any]:
    """Cheap call to verify connectivity and the API key(s)."""
    resp = await chat_completion(
        messages=[{"role": "user", "content": "Reply with the single word: OK"}],
        model=get_settings().groq_fast_model,
        temperature=0.0,
        max_tokens=5,
    )
    return {
        "model": resp.model,
        "reply": resp.choices[0].message.content,
        "keys_loaded": key_pool_size(),
    }
