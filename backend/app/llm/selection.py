"""The user's model choice, from Settings › Models.

`litellm_config.yaml` decides which model each *agent* uses. That per-agent
routing is the right default: the Architect wants deep reasoning, the Developer
runs constantly and wants the generous daily quota. But a user who has just
paid for DeepSeek wants *everything* on DeepSeek, without editing YAML.

So the selection here is an **override**, not a replacement. When one is set,
`app.llm.routing.route_for` puts it at the head of every agent's chain and
demotes the agent's configured models to fallbacks behind it:

    selection = deepseek/deepseek-v4-pro
    architect chain: deepseek-v4-pro -> gpt-oss-120b -> llama-3.3-70b -> ...

That is what makes selecting a DeepSeek model safe on an account with no
balance: the 402 raises `ProviderUnavailable`, the chain escalates, and the run
completes on Groq. The user sees a degraded provider, not a dead pipeline.

Stored in Redis so it survives a reload, and cached in-process so the sync
`route_for` can read it without awaiting.
"""
from __future__ import annotations

import logging

from app.llm.catalog import Provider, resolve
from app.redis.client import get_redis, key

log = logging.getLogger("asterion.llm.selection")

_KEY_SUFFIX = ("settings", "model")

# `None` = no override, use each agent's configured route. The cache is the
# read path (routing.route_for is sync and runs on every LLM call); Redis is
# the durability path.
_override: str | None = None
_loaded = False


def _redis_key() -> str:
    return key(*_KEY_SUFFIX)


def current() -> str | None:
    """The active override, or None. Sync — reads the in-process cache.

    Returns None until `load()` has run (called once from the app lifespan), so
    a cold process routes by YAML rather than blocking on Redis mid-request.
    """
    return _override


def loaded() -> bool:
    return _loaded


async def load() -> str | None:
    """Rehydrate the override from Redis into the cache. Called at startup."""
    global _override, _loaded
    try:
        raw = await (await get_redis()).get(_redis_key())
    except Exception as exc:  # noqa: BLE001 — a missing Redis must not block boot
        log.warning("Could not load model selection: %s", exc)
        return _override
    value = raw.decode() if isinstance(raw, bytes) else raw
    _override = value or None
    _loaded = True
    if _override:
        log.info("Model override active: %s", _override)
    return _override


async def set_override(model: str | None) -> str | None:
    """Persist and cache a new override. `None` clears it.

    Rejects a model that doesn't resolve to a configured provider — otherwise a
    typo in the UI would silently route every agent at a model that 404s, and
    the failure would only surface deep inside an agent run.
    """
    global _override
    if model:
        model = model.strip()
        provider, native = resolve(model)
        if not native:
            raise ValueError(f"'{model}' is not a usable model id")
        if provider is Provider.DEEPSEEK:
            from app.llm import deepseek_client

            if not deepseek_client.configured():
                raise ValueError("DeepSeek has no API key configured — set Deepseek_Key in .env")

    _override = model or None
    r = await get_redis()
    if _override:
        await r.set(_redis_key(), _override)
    else:
        await r.delete(_redis_key())
    log.info("Model override set to %s", _override or "(none — per-agent routing)")
    return _override
