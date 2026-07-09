"""Shared state for the pipeline graph.

Every node receives and returns partial updates to this TypedDict. Keys without
an explicit reducer use last-value-wins semantics; `events` accumulates.
"""
from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict


class Ticket(TypedDict, total=False):
    id: str
    title: str
    description: str                 # what to build
    acceptance_criteria: list[str]   # what counts as done
    test_checklist: list[str]        # how the human manually tests it
    dependencies: list[str]          # ids of tickets that must be done first
    effort: str                      # rough estimate, e.g. "S/M/L" or hours
    status: str                      # pending|in_progress|in_review|ready_for_test|passed|failed


class PipelineState(TypedDict, total=False):
    # ---- identity / input ----
    project_id: str
    raw_idea: str

    # ---- Agent 1: scope ----
    scope_doc: str
    scope_feedback: str              # feedback from a rejected APPROVE_SCOPE
    scope_qa: list[dict]             # [{"questions": [...], "answer": str}, ...]
    scope_pending_questions: list[str]

    # ---- Agent 2: architecture ----
    architecture_doc: str
    architecture_feedback: str       # feedback from a rejected APPROVE_ARCHITECTURE
    architecture_qa: list[dict]
    architecture_pending_questions: list[str]

    # ---- Agent 3: planner ----
    tickets: list[Ticket]
    tickets_feedback: str

    # ---- Agent 4/6: developer / debugger (per-ticket) ----
    current_ticket_index: int
    branch: str
    dev_notes: str
    debug_notes: str

    # ---- Agent 5: reviewer ----
    review_result: str               # approved|needs_fix
    review_notes: str
    review_rounds: int

    # ---- security scan (deterministic stage before review) ----
    security_findings: list[dict]    # blocking + advisory findings from the last scan
    security_rounds: int

    # ---- automated tests (deterministic stage after review) ----
    auto_test_rounds: int
    auto_test_summary: str           # "PASS (pytest)" | "FAILING: <tail>"

    # ---- risk-tier gate ----
    risk: dict                       # {"tier", "score", "category", "reasons"}

    # ---- test result (auto stage failure feedback, or human manual test) ----
    test_result: str                 # pass|fail
    test_feedback: str

    # ---- per-ticket outcome record (what was actually delivered) ----
    # ticket_id -> {"summary", "review_notes", "auto_test_summary", "risk",
    #               "files_changed"} — survives the per-ticket field resets so
    # the UI can show what every finished ticket produced, not just the last.
    ticket_outcomes: dict[str, dict]

    # ---- bookkeeping ----
    status: str
    current_agent: str
    pending_gate: str | None
    events: Annotated[list[dict[str, Any]], add]
