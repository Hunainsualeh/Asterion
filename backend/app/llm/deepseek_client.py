"""Async client for DeepSeek's OpenAI-compatible chat-completions API.

Built directly on `httpx` (already a dependency) rather than the `openai` SDK,
so adding a second provider costs no new package. In exchange this module owns
two things the SDK would have given us:

1. A response *shim* (`_Completion` & friends) with the exact attribute shape
   the Groq SDK returns — `resp.choices[0].message.tool_calls[0].function.name`,
   `resp.usage.prompt_tokens`, `resp.model`. `app.agents.base` reads those
   attributes off whatever provider answered, so both providers must look the
   same from the outside.
2. HTTP status -> `app.llm.errors` translation, so the escalation logic above
   never sees an `httpx` type.

Keys are pooled and rotated exactly like the Groq pool: a key that hits its
rate limit is put on cooldown *for that model only* and the next key is tried.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import get_settings
from app.llm import catalog
from app.llm.errors import (
    KeyPoolExhausted,
    LLMBadRequest,
    LLMConnectionError,
    OverCapacity,
    ProviderUnavailable,
    RateLimited,
    RequestTooLarge,
)

log = logging.getLogger("asterion.deepseek")

BASE_URL = "https://api.deepseek.com"
DEFAULT_COOLDOWN_S = 30.0
REQUEST_TIMEOUT_S = 60.0     # DeepSeek reasoning models are slower than Groq
DEFAULT_MAX_TOKENS = 4096
MAX_OUTPUT_TOKENS = 8192     # API ceiling; requesting more is a 400
_MODELS_TTL_S = 300.0


# --------------------------------------------------------------------------- response shim
@dataclass(frozen=True)
class _Function:
    name: str
    arguments: str


@dataclass(frozen=True)
class _ToolCall:
    id: str
    function: _Function
    type: str = "function"


@dataclass(frozen=True)
class _Message:
    role: str
    content: str | None
    tool_calls: list[_ToolCall] | None
    # DeepSeek reasoning models return their chain of thought separately. We
    # never feed it back into the transcript (it is not a valid assistant turn
    # for the next request) but it is useful in logs.
    reasoning_content: str | None = None


@dataclass(frozen=True)
class _Choice:
    index: int
    message: _Message
    finish_reason: str | None


@dataclass(frozen=True)
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class _Completion:
    id: str
    model: str
    choices: list[_Choice]
    usage: _Usage


def _parse_completion(payload: dict[str, Any]) -> _Completion:
    choices: list[_Choice] = []
    for raw in payload.get("choices") or []:
        msg = raw.get("message") or {}
        tool_calls = [
            _ToolCall(
                id=tc.get("id") or "",
                function=_Function(
                    name=(tc.get("function") or {}).get("name") or "",
                    arguments=(tc.get("function") or {}).get("arguments") or "{}",
                ),
            )
            for tc in (msg.get("tool_calls") or [])
        ]
        choices.append(
            _Choice(
                index=raw.get("index", 0),
                message=_Message(
                    role=msg.get("role") or "assistant",
                    content=msg.get("content"),
                    # `[]` and `None` mean the same thing to callers, but the
                    # Groq SDK hands back None when there were no tool calls
                    # and `base.py` does `message.tool_calls or []`. Match it.
                    tool_calls=tool_calls or None,
                    reasoning_content=msg.get("reasoning_content"),
                ),
                finish_reason=raw.get("finish_reason"),
            )
        )
    usage_raw = payload.get("usage") or {}
    return _Completion(
        id=payload.get("id") or "",
        model=payload.get("model") or "",
        choices=choices,
        usage=_Usage(
            prompt_tokens=usage_raw.get("prompt_tokens") or 0,
            completion_tokens=usage_raw.get("completion_tokens") or 0,
            total_tokens=usage_raw.get("total_tokens") or 0,
        ),
    )


# --------------------------------------------------------------------------- key pool
@dataclass
class _KeySlot:
    key: str
    cooldown_until: dict[str, float] = field(default_factory=dict)  # model -> monotonic deadline

    def available_for(self, model: str) -> bool:
        return time.monotonic() >= self.cooldown_until.get(model, 0.0)


_slots: list[_KeySlot] | None = None
_next_index = 0
_client: httpx.AsyncClient | None = None

# Surfaced to the UI via /api/models so a dead key explains itself instead of
# silently degrading to Groq. Set on every ProviderUnavailable.
_last_fatal: str | None = None


def _mask(key: str) -> str:
    return f"{key[:6]}...{key[-4:]}" if len(key) > 12 else "***"


def configured() -> bool:
    return bool(get_settings().deepseek_api_keys)


def last_fatal_error() -> str | None:
    return _last_fatal


def _get_slots() -> list[_KeySlot]:
    global _slots
    if _slots is None:
        keys = get_settings().deepseek_api_keys
        if not keys:
            raise ProviderUnavailable(
                "No DeepSeek API key configured. Set Deepseek_Key (or DEEPSEEK_API_KEY) in the project .env file.",
                provider="deepseek",
            )
        _slots = [_KeySlot(key=k) for k in keys]
        log.info("DeepSeek key pool: %d key(s) loaded", len(_slots))
    return _slots


def key_pool_size() -> int:
    return len(_get_slots()) if configured() else 0


def _pick_slot(model: str) -> _KeySlot | None:
    global _next_index
    slots = _get_slots()
    for offset in range(len(slots)):
        idx = (_next_index + offset) % len(slots)
        slot = slots[idx]
        if slot.available_for(model):
            _next_index = (idx + 1) % len(slots)
            return slot
    return None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=BASE_URL, timeout=REQUEST_TIMEOUT_S)
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# --------------------------------------------------------------------------- errors
def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err or body)[:200]


def _retry_after(response: httpx.Response) -> float:
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    return DEFAULT_COOLDOWN_S


def _raise_for_status(response: httpx.Response, model: str) -> None:
    """Translate a DeepSeek HTTP error into the neutral taxonomy.

    Status codes per DeepSeek's error reference. The one that matters most in
    practice is 402: DeepSeek is prepaid, and an account at zero balance
    answers *every* completion with 402 while still happily serving
    `GET /models` and accepting the key. Treated as ProviderUnavailable so the
    routing chain escalates past DeepSeek instead of retrying a wall.
    """
    global _last_fatal
    if response.status_code < 400:
        return

    message = _error_message(response)
    status = response.status_code
    common = {"provider": "deepseek", "model": model, "status_code": status}

    if status in (401, 403):
        _last_fatal = f"Authentication failed ({status}): {message}"
        raise ProviderUnavailable(f"DeepSeek rejected the API key: {message}", **common)
    if status == 402:
        _last_fatal = f"Insufficient balance: {message}"
        raise ProviderUnavailable(
            "DeepSeek account has no remaining balance — top it up at "
            "https://platform.deepseek.com/top_up to use DeepSeek models.",
            **common,
        )
    if status == 429:
        raise RateLimited(message, retry_after=_retry_after(response), **common)
    if status == 413:
        raise RequestTooLarge(message, **common)
    if status in (500, 502, 503, 504):
        raise OverCapacity(message, **common)
    raise LLMBadRequest(message, **common)


# --------------------------------------------------------------------------- completions
async def chat_completion(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
    model: str,
    temperature: float = 0.15,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
    response_format: dict[str, Any] | None = None,
    timeout_s: float = REQUEST_TIMEOUT_S,
) -> _Completion:
    """Chat completion against DeepSeek, rotating keys on rate limits.

    Mirrors `groq_client.chat_completion`'s contract exactly — same arguments,
    same return shape, same exception taxonomy — so `app.llm.client` can pick
    between them on model id alone.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    if max_tokens is not None:
        payload["max_tokens"] = min(max_tokens, MAX_OUTPUT_TOKENS)
    if response_format is not None:
        payload["response_format"] = response_format

    slots_tried = 0
    last_exc: Exception | None = None
    timed_out = False
    total_slots = len(_get_slots())

    # Only 429 (this key's quota) and a timeout (this key is wedged) rotate.
    # Everything `_raise_for_status` raises — ProviderUnavailable, OverCapacity,
    # RequestTooLarge, LLMBadRequest — is a wall every key hits identically, so
    # it propagates straight out and the *model* chain escalates instead.
    while slots_tried < total_slots:
        slot = _pick_slot(model)
        if slot is None:
            break  # every key is on cooldown for this model
        slots_tried += 1
        headers = {"Authorization": f"Bearer {slot.key}", "Content-Type": "application/json"}
        try:
            response = await _http().post(
                "/chat/completions", json=payload, headers=headers, timeout=timeout_s
            )
            _raise_for_status(response, model)
            return _parse_completion(response.json())
        except RateLimited as exc:
            last_exc = exc
            cooldown = exc.retry_after or DEFAULT_COOLDOWN_S
            slot.cooldown_until[model] = time.monotonic() + cooldown
            log.warning(
                "DeepSeek key %s rate-limited for %s (%.0fs cooldown); rotating",
                _mask(slot.key), model, cooldown,
            )
        except httpx.TimeoutException as exc:
            last_exc = exc
            timed_out = True
            slot.cooldown_until[model] = time.monotonic() + DEFAULT_COOLDOWN_S
            log.warning("DeepSeek key %s timed out for %s; rotating", _mask(slot.key), model)
        except httpx.HTTPError as exc:
            raise LLMConnectionError(
                f"Could not reach DeepSeek: {exc}", provider="deepseek", model=model
            ) from exc

    if timed_out:
        # Match the Groq client: a pool that only ever times out is, from the
        # caller's point of view, an exhausted pool — escalate to the next model.
        raise KeyPoolExhausted(
            f"every DeepSeek key timed out for {model}", provider="deepseek", model=model
        ) from last_exc
    if last_exc is not None:
        raise last_exc
    raise KeyPoolExhausted(
        f"every DeepSeek key is on cooldown for {model}", provider="deepseek", model=model
    )


# --------------------------------------------------------------------------- discovery
_models_cache: tuple[float, list[str]] | None = None


async def list_models(force: bool = False) -> list[str]:
    """Live model ids from `GET /models`, cached for `_MODELS_TTL_S`.

    This endpoint answers even when the account balance is zero, which is what
    lets the model picker stay populated (and explain itself) on a dead key.
    Returns `[]` — never raises — when DeepSeek isn't configured or reachable,
    because the picker must still render the Groq half of the catalog.
    """
    global _models_cache, _last_fatal
    if not configured():
        return []
    if not force and _models_cache and time.monotonic() - _models_cache[0] < _MODELS_TTL_S:
        return _models_cache[1]

    key = get_settings().deepseek_api_keys[0]
    try:
        response = await _http().get(
            "/models", headers={"Authorization": f"Bearer {key}"}, timeout=10.0
        )
        if response.status_code in (401, 403):
            _last_fatal = f"Authentication failed ({response.status_code})"
            return []
        response.raise_for_status()
        ids = [m["id"] for m in (response.json().get("data") or []) if m.get("id")]
    except Exception as exc:  # noqa: BLE001 — discovery is best-effort
        log.warning("DeepSeek model discovery failed: %s", exc)
        return _models_cache[1] if _models_cache else []

    catalog.record_live_deepseek_models(ids)
    _models_cache = (time.monotonic(), ids)
    log.info("DeepSeek models discovered: %s", ", ".join(ids) or "(none)")
    return ids


_balance_cache: tuple[float, dict[str, Any] | None] | None = None


async def balance(force: bool = False) -> dict[str, Any] | None:
    """`GET /user/balance` — the cheapest way to tell a live key from a broke
    one *before* burning a completion on it. `None` if unavailable.

    Cached on the same TTL as model discovery: the Settings panel calls this on
    every open, and a topped-up balance doesn't need to be visible in under
    five minutes.
    """
    global _balance_cache
    if not configured():
        return None
    if not force and _balance_cache and time.monotonic() - _balance_cache[0] < _MODELS_TTL_S:
        return _balance_cache[1]

    key = get_settings().deepseek_api_keys[0]
    try:
        response = await _http().get(
            "/user/balance", headers={"Authorization": f"Bearer {key}"}, timeout=10.0
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 — best-effort, never blocks the UI
        log.debug("DeepSeek balance check failed: %s", exc)
        return _balance_cache[1] if _balance_cache else None

    _balance_cache = (time.monotonic(), payload)
    return payload


async def status_note() -> str | None:
    """A one-line, user-facing explanation of why DeepSeek won't work — or None
    when it will.

    Checked *before* the user picks a DeepSeek model, not after a run dies.
    DeepSeek is prepaid and fails open in a confusing way: a zero-balance
    account still authenticates, still lists its models, and only reveals the
    problem when a completion comes back 402. `is_available` in the balance
    payload is the provider's own verdict on the same question.
    """
    if not configured():
        return "No API key. Set Deepseek_Key in the project .env file."
    if _last_fatal:
        return _last_fatal

    payload = await balance()
    if payload is None or payload.get("is_available"):
        return None

    infos = payload.get("balance_infos") or []
    amount = f"{infos[0].get('total_balance')} {infos[0].get('currency', '')}".strip() if infos else "empty"
    return (
        f"Account balance is {amount}. DeepSeek is prepaid, so every request fails with "
        "HTTP 402 until you top up at https://platform.deepseek.com/top_up. You can still "
        "select a DeepSeek model — runs will fall back to Groq automatically."
    )


async def health_check() -> dict[str, Any]:
    """Verify connectivity, the key, and that a completion actually runs."""
    models = await list_models()
    resp = await chat_completion(
        messages=[{"role": "user", "content": "Reply with the single word: OK"}],
        model=models[0] if models else "deepseek-v4-flash",
        temperature=0.0,
        max_tokens=5,
    )
    return {
        "model": resp.model,
        "reply": resp.choices[0].message.content,
        "keys_loaded": key_pool_size(),
        "models": models,
    }
