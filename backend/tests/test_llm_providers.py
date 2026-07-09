"""Multi-provider LLM layer: model->provider resolution and the selection
override that Settings › Models writes."""
from __future__ import annotations

import pytest

from app.llm import routing, selection
from app.llm.catalog import DEEPSEEK_MODELS, GROQ_MODELS, Provider, known, resolve


@pytest.fixture(autouse=True)
def _clear_override():
    """`selection` caches the override in a module global that `route_for`
    reads synchronously. Leaking it between tests would silently rewrite every
    other test's expected chain."""
    before = selection._override
    selection._override = None
    yield
    selection._override = before


# --------------------------------------------------------------------------- resolution
def test_deepseek_prefix_resolves_and_strips():
    assert resolve("deepseek/deepseek-v4-pro") == (Provider.DEEPSEEK, "deepseek-v4-pro")


def test_bare_deepseek_id_resolves_to_deepseek():
    assert resolve("deepseek-v4-flash") == (Provider.DEEPSEEK, "deepseek-v4-flash")


def test_groq_compound_keeps_its_slash():
    """`groq/compound` is a Groq model whose id happens to start with 'groq/'.
    Splitting on the first slash would send 'compound' to Groq and 404."""
    assert resolve("groq/compound") == (Provider.GROQ, "groq/compound")


def test_deepseek_distill_on_groq_stays_on_groq():
    """DeepSeek *weights*, Groq *host*. Only the `deepseek/` prefix and the
    DeepSeek catalog route to DeepSeek's own API."""
    assert resolve("deepseek-r1-distill-llama-70b") == (Provider.GROQ, "deepseek-r1-distill-llama-70b")


def test_groq_models_resolve_to_groq():
    for model in GROQ_MODELS:
        assert resolve(model.qualified_id)[0] is Provider.GROQ, model.id


def test_deepseek_models_are_namespaced():
    for model in DEEPSEEK_MODELS:
        assert model.qualified_id == f"deepseek/{model.id}"
        assert resolve(model.qualified_id) == (Provider.DEEPSEEK, model.id)


def test_known_accepts_bare_and_qualified_deepseek_ids():
    assert known("deepseek/deepseek-v4-pro") is known("deepseek-v4-pro")
    assert known("deepseek-v4-pro") is not None
    assert known("not-a-real-model") is None


# --------------------------------------------------------------------------- override
def test_no_override_uses_configured_route():
    assert routing.chain_for("architect") == routing.configured_route_for("architect").chain


def test_override_becomes_primary_and_demotes_configured_chain():
    configured = routing.configured_route_for("architect").chain
    selection._override = "deepseek/deepseek-v4-pro"

    chain = routing.chain_for("architect")
    assert chain[0] == "deepseek/deepseek-v4-pro"
    # Nothing is lost: the whole configured chain survives as fallbacks, which
    # is what lets a 402 from DeepSeek degrade onto Groq instead of failing.
    assert chain[1:] == configured


def test_override_applies_to_every_agent():
    selection._override = "deepseek/deepseek-v4-flash"
    for agent in ("architect", "developer", "answer", "coder", "reviewer"):
        assert routing.chain_for(agent)[0] == "deepseek/deepseek-v4-flash", agent


def test_override_equal_to_primary_is_a_no_op():
    primary = routing.configured_route_for("architect").model
    selection._override = primary
    assert routing.chain_for("architect") == routing.configured_route_for("architect").chain


def test_override_never_duplicates_a_model_in_the_chain():
    # Pick a model that is already a *fallback* for this agent: it must move to
    # the head, not appear twice (a duplicate would burn a whole retry round
    # re-calling a model that just failed).
    fallback = routing.configured_route_for("architect").chain[1]
    selection._override = fallback

    chain = routing.chain_for("architect")
    assert chain[0] == fallback
    assert len(chain) == len(set(chain)), chain


def test_all_routes_ignores_the_override():
    """`all_routes` describes the YAML, not the live routing — the models API
    and the routing tests both depend on that."""
    before = routing.all_routes()
    selection._override = "deepseek/deepseek-v4-pro"
    assert routing.all_routes() == before
