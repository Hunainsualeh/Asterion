"""Provider-neutral LLM exceptions.

Asterion talks to more than one LLM provider (Groq, DeepSeek), each with its
own SDK and its own exception hierarchy. The retry/escalation logic that keeps
the pipeline alive — `app.agents.base._complete_with_retry` and
`app.dag.workflows._complete` — must not care which SDK raised: it only needs
to know *what kind* of failure happened, because each kind implies a different
recovery:

    MalformedToolCall   -> resample the same model (sampling noise)
    OverCapacity        -> back off, retry the same model (transient 5xx)
    RateLimited         -> escalate to the next model (quota is gone *now*)
    ProviderUnavailable -> escalate to the next model (key/billing is broken)
    RequestTooLarge     -> escalate to the next model (a bigger context helps)
    LLMBadRequest       -> raise (we built a bad request; retrying won't help)

Every provider client in `app.llm` translates its SDK's exceptions into these
at the boundary, so nothing above `app.llm` imports `groq` or `httpx`.
"""
from __future__ import annotations


class LLMError(RuntimeError):
    """Base for every error raised out of `app.llm`."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.retry_after = retry_after

    def __str__(self) -> str:  # noqa: D105
        base = super().__str__()
        tag = "/".join(p for p in (self.provider, self.model) if p)
        return f"[{tag}] {base}" if tag else base


class LLMConnectionError(LLMError):
    """The provider was unreachable (DNS, TLS, socket)."""


class LLMTimeout(LLMError):
    """The request exceeded our own deadline."""


class OverCapacity(LLMError):
    """Transient provider-side 5xx. Backing off and retrying usually works."""


class RateLimited(LLMError):
    """Quota/rate limit hit for this (key, model) pair right now."""


class KeyPoolExhausted(RateLimited):
    """Every pooled key is on cooldown for the requested model.

    Kept as a distinct name because callers outside `app.llm` already branch on
    it, and because it means something stronger than `RateLimited`: rotating
    keys has already been tried and there is nothing left to rotate to.
    """


class ProviderUnavailable(LLMError):
    """The provider refuses to serve this key at all: missing/invalid key,
    exhausted prepaid balance (DeepSeek 402), suspended account.

    Distinct from `RateLimited` because waiting does not fix it — only the user
    can, by fixing billing or the key. The pipeline escalates past it to the
    next model in the chain rather than stalling on a retry loop.
    """


class LLMBadRequest(LLMError):
    """The provider rejected the request itself (400)."""


class MalformedToolCall(LLMBadRequest):
    """The model emitted an unparseable inline `<function=...>` blob instead of
    a structured tool call. Transient sampling noise — resample."""


class RequestTooLarge(LLMBadRequest):
    """The request exceeds the model's per-request token ceiling. A different
    key hits the same wall; only a larger-context model helps."""


__all__ = [
    "KeyPoolExhausted",
    "LLMBadRequest",
    "LLMConnectionError",
    "LLMError",
    "LLMTimeout",
    "MalformedToolCall",
    "OverCapacity",
    "ProviderUnavailable",
    "RateLimited",
    "RequestTooLarge",
]
