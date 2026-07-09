"""The catalog of models Asterion can talk to, and the rule that maps a model
id to the provider that serves it.

Provider resolution is deliberately *not* "split on the first slash". Two real
model ids make that wrong:

    groq/compound                  -> a Groq model whose id literally starts
                                      with "groq/" (used by app/tools/research.py)
    deepseek-r1-distill-llama-70b  -> a DeepSeek-*distilled* model, but hosted
                                      by Groq, not by DeepSeek

So the rule is explicit instead:

    1. an explicit `deepseek/` prefix  -> DeepSeek (prefix stripped)
    2. a bare id in the DeepSeek catalog -> DeepSeek
    3. everything else                 -> Groq

`DEEPSEEK_MODELS` is the static fallback catalog. The live list is fetched from
DeepSeek's own `GET /models` endpoint (see `deepseek_client.list_models`) and
merged over it, so a model DeepSeek ships tomorrow shows up without a code
change. The static list exists so the app still boots, and the UI still renders
a sensible picker, with no network and no key.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Provider(str, Enum):
    GROQ = "groq"
    DEEPSEEK = "deepseek"


DEEPSEEK_PREFIX = "deepseek/"


@dataclass(frozen=True)
class ModelInfo:
    """One selectable model, as presented to the UI."""

    id: str                      # provider-native id, e.g. "deepseek-v4-pro"
    provider: Provider
    label: str
    description: str
    tier: str                    # "reasoning" | "balanced" | "fast" | "long-context" | "utility"
    supports_tools: bool = True
    # Reasoning models spend hidden "thinking" tokens before answering. The
    # tool-calling loop caps these (see groq_client / deepseek_client) because
    # every agent turn wants a short structured answer, not an essay.
    reasoning: bool = False

    @property
    def qualified_id(self) -> str:
        """The id to store in settings / send over the API. DeepSeek ids are
        namespaced so they can never collide with a Groq-hosted model of the
        same name (see the `deepseek-r1-distill-llama-70b` case above)."""
        if self.provider is Provider.DEEPSEEK:
            return f"{DEEPSEEK_PREFIX}{self.id}"
        return self.id


# --------------------------------------------------------------------------- DeepSeek
# Verified live against `GET https://api.deepseek.com/models` on 2026-07-09.
# NOTE: these are *not* the `deepseek-chat` / `deepseek-reasoner` ids that most
# documentation (and most LLM training data) still shows — DeepSeek renamed the
# served models to the v4 line. Always trust the live endpoint over this list.
DEEPSEEK_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        id="deepseek-v4-pro",
        provider=Provider.DEEPSEEK,
        label="DeepSeek V4 Pro",
        description="Deep reasoning. Strongest DeepSeek model — best for architecture and debugging.",
        tier="reasoning",
        reasoning=True,
    ),
    ModelInfo(
        id="deepseek-v4-flash",
        provider=Provider.DEEPSEEK,
        label="DeepSeek V4 Flash",
        description="Fast, cheap, general-purpose. Good default for chat and routine agent turns.",
        tier="fast",
    ),
)

# --------------------------------------------------------------------------- Groq
# Mirrors backend/litellm_config.yaml. That YAML stays the source of truth for
# *which agent uses which model*; this list is only what the model picker shows.
GROQ_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        id="openai/gpt-oss-120b",
        provider=Provider.GROQ,
        label="GPT-OSS 120B",
        description="Deep reasoning with the most reliable tool-calling on Groq. Tight 8K TPM cap.",
        tier="reasoning",
        reasoning=True,
    ),
    ModelInfo(
        id="llama-3.3-70b-versatile",
        provider=Provider.GROQ,
        label="Llama 3.3 70B",
        description="Balanced reasoning and code quality with enough TPM headroom for long turns.",
        tier="balanced",
    ),
    ModelInfo(
        id="meta-llama/llama-4-scout-17b-16e-instruct",
        provider=Provider.GROQ,
        label="Llama 4 Scout 17B",
        description="Highest TPM ceiling (30K) and vision input. Used for long diffs and documents.",
        tier="long-context",
    ),
    ModelInfo(
        id="openai/gpt-oss-20b",
        provider=Provider.GROQ,
        label="GPT-OSS 20B",
        description="Emergency fallback on a separate daily quota. Degraded, but keeps runs alive.",
        tier="utility",
        reasoning=True,
    ),
    ModelInfo(
        id="llama-3.1-8b-instant",
        provider=Provider.GROQ,
        label="Llama 3.1 8B Instant",
        description="High-volume tier (14,400 requests/day). Weakest tool-calling — escalates often.",
        tier="fast",
    ),
)

_STATIC: dict[str, ModelInfo] = {m.qualified_id: m for m in (*GROQ_MODELS, *DEEPSEEK_MODELS)}
_DEEPSEEK_BARE_IDS: frozenset[str] = frozenset(m.id for m in DEEPSEEK_MODELS)


def resolve(model_id: str) -> tuple[Provider, str]:
    """Map a model id to `(provider, provider-native id)`.

    >>> resolve("deepseek/deepseek-v4-pro")
    (<Provider.DEEPSEEK: 'deepseek'>, 'deepseek-v4-pro')
    >>> resolve("deepseek-v4-flash")           # bare, but a known DeepSeek id
    (<Provider.DEEPSEEK: 'deepseek'>, 'deepseek-v4-flash')
    >>> resolve("groq/compound")               # NOT a provider prefix
    (<Provider.GROQ: 'groq'>, 'groq/compound')
    >>> resolve("deepseek-r1-distill-llama-70b")   # DeepSeek weights, Groq host
    (<Provider.GROQ: 'groq'>, 'deepseek-r1-distill-llama-70b')
    """
    model_id = (model_id or "").strip()
    if model_id.startswith(DEEPSEEK_PREFIX):
        return Provider.DEEPSEEK, model_id[len(DEEPSEEK_PREFIX) :]
    if model_id in _DEEPSEEK_BARE_IDS or model_id in _live_deepseek_ids():
        return Provider.DEEPSEEK, model_id
    return Provider.GROQ, model_id


# Live DeepSeek ids discovered at runtime. Populated by `deepseek_client`; kept
# here (not there) so `resolve` never has to import a provider client and risk
# a circular import.
_live_ids: set[str] = set()


def _live_deepseek_ids() -> set[str]:
    return _live_ids


def record_live_deepseek_models(ids: list[str]) -> None:
    """Called by `deepseek_client` after a successful `GET /models`, so ids we
    didn't ship in `DEEPSEEK_MODELS` still resolve to the DeepSeek provider."""
    _live_ids.update(ids)


def known(model_id: str) -> ModelInfo | None:
    """Look up catalog metadata, tolerating a bare DeepSeek id."""
    if model_id in _STATIC:
        return _STATIC[model_id]
    provider, native = resolve(model_id)
    if provider is Provider.DEEPSEEK:
        return _STATIC.get(f"{DEEPSEEK_PREFIX}{native}")
    return None


def static_models() -> tuple[ModelInfo, ...]:
    return (*GROQ_MODELS, *DEEPSEEK_MODELS)


def describe_unknown(model_id: str, provider: Provider) -> ModelInfo:
    """Wrap a model id the static catalog has never heard of (i.e. one DeepSeek
    started serving after this file was written) so the UI can still list it."""
    return ModelInfo(
        id=model_id,
        provider=provider,
        label=model_id,
        description="Reported live by the provider; not in Asterion's static catalog.",
        tier="balanced",
        reasoning="reasoner" in model_id or "-pro" in model_id,
    )
