"""Config-driven per-agent model routing.

Parses `backend/litellm_config.yaml` (standard LiteLLM proxy format — the
same file works unmodified in front of a real LiteLLM gateway later) and
resolves each agent name to its model plus an ordered fallback chain.
Nothing outside this module knows which model an agent uses; swapping a tier
is a YAML edit, not a code change.

On top of that sits the user's override from Settings › Models (see
`app.llm.selection`). An override is *prepended* to every agent's chain rather
than replacing it, so a provider that fails — DeepSeek on an unpaid account
answers every request with 402 — degrades back onto the configured Groq chain
instead of killing the run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import BACKEND_DIR, get_settings
from app.llm import selection

log = logging.getLogger("asterion.routing")

CONFIG_PATH = BACKEND_DIR / "litellm_config.yaml"


def _strip_provider(model: str) -> str:
    """LiteLLM prefixes the provider ('groq/openai/gpt-oss-120b'); the Groq
    SDK wants the bare model id ('openai/gpt-oss-120b')."""
    return model.removeprefix("groq/")


@dataclass(frozen=True)
class Route:
    """One agent's resolved routing: primary model + escalation chain."""

    model: str
    fallbacks: tuple[str, ...] = ()

    @property
    def chain(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for m in (self.model, *self.fallbacks):
            if m not in seen:
                seen.add(m)
                out.append(m)
        return out


def _default_route() -> Route:
    s = get_settings()
    return Route(model=s.groq_model, fallbacks=("llama-3.3-70b-versatile",))


@lru_cache
def _load(path_str: str, mtime: float) -> dict[str, Route]:
    """Cached per (path, mtime): editing the YAML takes effect on the next
    call without a process restart."""
    path = Path(path_str)
    if not path.exists():
        log.warning("Routing config %s not found — every agent uses the default model", path)
        return {}

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    alias_to_model: dict[str, str] = {}
    for entry in raw.get("model_list") or []:
        alias = entry.get("model_name", "")
        model = _strip_provider((entry.get("litellm_params") or {}).get("model", ""))
        if alias and model:
            alias_to_model[alias] = model

    alias_fallbacks: dict[str, list[str]] = {}
    for mapping in (raw.get("router_settings") or {}).get("fallbacks") or []:
        for alias, fb_aliases in mapping.items():
            alias_fallbacks[alias] = list(fb_aliases)

    routes: dict[str, Route] = {}
    for alias, model in alias_to_model.items():
        fb_models = tuple(
            alias_to_model[fb] for fb in alias_fallbacks.get(alias, []) if fb in alias_to_model
        )
        routes[alias] = Route(model=model, fallbacks=fb_models)

    log.info("Model routing loaded: %d agents from %s", len(routes), path.name)
    return routes


def _mtime() -> float:
    try:
        return CONFIG_PATH.stat().st_mtime
    except OSError:
        return 0.0


def configured_route_for(agent: str) -> Route:
    """The route as written in the YAML, ignoring any user override."""
    return _load(str(CONFIG_PATH), _mtime()).get(agent) or _default_route()


def route_for(agent: str) -> Route:
    """The route actually used for a call: the user's selected model first (if
    any), then everything the YAML configured for this agent as fallbacks."""
    base = configured_route_for(agent)
    override = selection.current()
    if not override or override == base.model:
        return base
    # The configured primary becomes the first fallback — it is, by definition,
    # the best model for this agent absent a user preference.
    return Route(model=override, fallbacks=(base.model, *base.fallbacks))


def chain_for(agent: str) -> list[str]:
    return route_for(agent).chain


def all_routes() -> dict[str, Route]:
    """Every agent alias in the YAML, unaffected by the override."""
    return dict(_load(str(CONFIG_PATH), _mtime()))
