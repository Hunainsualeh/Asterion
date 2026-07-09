"""Intent heuristics: the deterministic fast paths that must never regress."""
from __future__ import annotations

from app.dag.workflows import build_nodes_for
from app.orchestration.intent import Intent, classify_heuristic


def test_weather_question_routes_to_weather_fast_path():
    intent = classify_heuristic("What is the weather in Lahore?")
    assert intent is not None
    assert intent.kind == "weather"
    assert intent.lane == "task"
    assert intent.slots.get("location") == "Lahore"


def test_greeting_is_chat():
    for text in ("hi", "Hello!", "thanks", "good morning"):
        intent = classify_heuristic(text)
        assert intent is not None and intent.kind == "chat", text


def test_build_request_routes_to_project_lane():
    intent = classify_heuristic("Build me a todo app with user accounts and reminders")
    assert intent is not None
    assert intent.kind == "software_project"
    assert intent.lane == "project"


def test_single_script_is_coding_not_project():
    intent = classify_heuristic("Write a python script to rename all files by date")
    assert intent is not None
    assert intent.kind == "coding"
    assert intent.lane == "task"


def test_short_question_is_qa():
    intent = classify_heuristic("Who invented the transistor?")
    assert intent is not None
    assert intent.kind == "qa"


def test_ambiguous_text_defers_to_llm():
    assert classify_heuristic("I have been thinking about our approach to onboarding lately") is None


# ---- DAG shapes ----
def test_weather_dag_is_single_node():
    nodes = build_nodes_for(Intent(kind="weather", complexity="trivial", slots={"location": "Lahore"}), "w?")
    assert len(nodes) == 1
    assert nodes[0].agent == "weather"
    assert nodes[0].is_final


def test_moderate_dag_is_canonical_fanout():
    nodes = build_nodes_for(Intent(kind="research", complexity="moderate"), "q")
    by_id = {n.id: n for n in nodes}
    assert set(by_id) == {"research", "analyze", "summarize"}
    assert by_id["research"].deps == [] and by_id["analyze"].deps == []
    assert sorted(by_id["summarize"].deps) == ["analyze", "research"]
    assert by_id["summarize"].allow_failed_deps  # degrades, never empty-handed


def test_complex_dag_starts_with_planner_root():
    nodes = build_nodes_for(Intent(kind="research", complexity="complex"), "q")
    assert len(nodes) == 1 and nodes[0].agent == "planner"
