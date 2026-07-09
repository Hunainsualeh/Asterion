"""Model catalog + selection endpoints.

    GET  /api/models             every model the app can route to, per provider
    PUT  /api/models/selection   pick one (or null to restore per-agent routing)
    GET  /api/models/health      live per-provider check (spends one token)

The catalog is a merge of Asterion's static list (`app.llm.catalog`) and the
live `GET /models` DeepSeek serves, so a model DeepSeek ships tomorrow appears
in the picker without a redeploy.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.llm import catalog, deepseek_client, selection
from app.llm.catalog import Provider

router = APIRouter(tags=["models"])


class ModelSelectionRequest(BaseModel):
    # `None` clears the override and restores litellm_config.yaml's per-agent
    # routing. Absent and null mean the same thing.
    model: str | None = None


def _serialize(info: catalog.ModelInfo, *, available: bool) -> dict:
    return {
        "id": info.qualified_id,
        "provider": info.provider.value,
        "label": info.label,
        "description": info.description,
        "tier": info.tier,
        "reasoning": info.reasoning,
        "supports_tools": info.supports_tools,
        "available": available,
    }


async def _deepseek_models() -> list[catalog.ModelInfo]:
    """Live ids, falling back to the static catalog when discovery fails (no
    key, no network). Static entries keep their curated label/description; ids
    we've never seen get a generated one."""
    live_ids = await deepseek_client.list_models()
    if not live_ids:
        return list(catalog.DEEPSEEK_MODELS)

    by_id = {m.id: m for m in catalog.DEEPSEEK_MODELS}
    return [by_id.get(mid) or catalog.describe_unknown(mid, Provider.DEEPSEEK) for mid in live_ids]


@router.get("/models")
async def list_models() -> dict:
    """The picker's entire data source: what exists, what's usable, what's on."""
    from app.config import get_settings

    settings = get_settings()
    groq_ok = bool(settings.groq_api_keys)
    deepseek_ok = deepseek_client.configured()

    models = [_serialize(m, available=groq_ok) for m in catalog.GROQ_MODELS]
    models += [_serialize(m, available=deepseek_ok) for m in await _deepseek_models()]

    # A configured-but-broken key is the interesting case: DeepSeek is prepaid,
    # so a zero-balance account authenticates, lists its models, and 402s every
    # completion. `status_note` asks the provider about its own balance so the
    # warning appears *before* the user picks a model that can't answer — not
    # after a run mysteriously completes on Groq. The models stay selectable:
    # the routing chain degrades cleanly, and a top-up needs no restart.
    deepseek_note = await deepseek_client.status_note()

    return {
        "providers": [
            {
                "id": "groq",
                "label": "Groq",
                "configured": groq_ok,
                "keys": len(settings.groq_api_keys),
                "note": None if groq_ok else "No API key. Set Asterion_Secret_key in .env.",
            },
            {
                "id": "deepseek",
                "label": "DeepSeek",
                "configured": deepseek_ok,
                "keys": len(settings.deepseek_api_keys),
                "note": deepseek_note,
            },
        ],
        "models": models,
        # None => per-agent routing from litellm_config.yaml.
        "selected": selection.current(),
    }


@router.put("/models/selection")
async def set_selection(req: ModelSelectionRequest) -> dict:
    try:
        selected = await selection.set_override(req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "selected": selected}


@router.get("/models/health")
async def health() -> dict:
    """Round-trips a 5-token completion through each configured provider. The
    only way to distinguish a valid key from a valid-but-unfunded one."""
    from app.llm.client import health_check

    report = await health_check()
    balance = await deepseek_client.balance()
    if balance is not None:
        report.setdefault("deepseek", {})["balance"] = balance
    return report
