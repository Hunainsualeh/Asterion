"""Routing config: every pipeline agent resolves to its assigned tier."""
from __future__ import annotations

from app.llm.routing import all_routes, chain_for, route_for

EXPECTED_PRIMARY = {
    "architect": "openai/gpt-oss-120b",
    "debugger": "openai/gpt-oss-120b",
    "supervisor": "llama-3.3-70b-versatile",
    "planner": "llama-3.3-70b-versatile",
    "reviewer": "llama-3.3-70b-versatile",
    "security": "llama-3.3-70b-versatile",
    "scope": "llama-3.3-70b-versatile",
    "research": "meta-llama/llama-4-scout-17b-16e-instruct",
    "developer": "llama-3.1-8b-instant",
    "test": "llama-3.1-8b-instant",
    "docs": "llama-3.1-8b-instant",
    # task-lane DAG executors (app/dag/workflows.py)
    "analyze": "llama-3.3-70b-versatile",
    "summarize": "llama-3.3-70b-versatile",
    "answer": "llama-3.3-70b-versatile",
    "coder": "llama-3.3-70b-versatile",
    "designer": "llama-3.3-70b-versatile",
}


def test_all_agents_route_to_assigned_models():
    for agent, model in EXPECTED_PRIMARY.items():
        assert route_for(agent).model == model, agent


def test_every_agent_has_at_least_one_fallback():
    for agent in EXPECTED_PRIMARY:
        chain = chain_for(agent)
        assert len(chain) >= 2, f"{agent} has no fallback: {chain}"
        assert len(chain) == len(set(chain)), f"{agent} chain has duplicates: {chain}"


def test_developer_escalates_through_70b_then_scout_then_utility():
    chain = chain_for("developer")
    assert chain == [
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "openai/gpt-oss-20b",
    ]


def test_reasoning_agents_have_deep_chains():
    # Daily caps on 70B/120B really do run out — every reasoning agent must
    # be able to degrade to a model with a separate quota.
    for agent in ["architect", "debugger", "scope", "planner", "reviewer"]:
        assert len(chain_for(agent)) >= 3, agent


def test_unknown_agent_gets_default_route():
    route = route_for("nonexistent-agent")
    assert route.model  # falls back to settings default, never empty


def test_config_file_parsed():
    # every agent alias + the shared 'utility' fallback alias
    assert len(all_routes()) == len(EXPECTED_PRIMARY) + 1
