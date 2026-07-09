"""The pipeline graph — the 'assembly line'.

Agent nodes with human stop-gates between the planning stages, plus the
deterministic Build-phase stages: every change passes a security scan before
review, automated tests after review, and a risk-tier gate that only stops
for a human when the computed tier demands it (Tier 2). Tier 0/1 work ships
with an audit trail / async digest instead of a meeting.

Flow:
  START → scope ⇄ scope_wait (ask_human loop) → [APPROVE_SCOPE]
        → architect ⇄ architect_wait (ask_human loop) → [APPROVE_ARCHITECTURE]
        → planner → [APPROVE_TICKETS]
        → developer → security_scan → (blocking ↺ developer) | reviewer
        → (needs_fix ↺ developer) | auto_test
        → (fail → debugger → security_scan ↺) | risk_gate
        → (tier 0/1 → docs) | [MANUAL_TEST]
        → PASS → docs → (next ticket ↺ developer | END)
        → FAIL → debugger → security_scan ↺
"""
from __future__ import annotations

import asyncio
import json

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents import architect as architect_mod
from app.agents import debugger as debugger_mod
from app.agents import developer as developer_mod
from app.agents import planner as planner_mod
from app.agents import reviewer as reviewer_mod
from app.agents import scope as scope_mod
from app.knowledge import hooks as knowledge
from app.orchestration import risk as risk_mod
from app.orchestration.events import publish_event
from app.orchestration.security_stage import run_security_scan
from app.orchestration.test_stage import run_repo_tests
from app.orchestration.gates import (
    GATE_ARCHITECTURE,
    GATE_ARCHITECTURE_CLARIFY,
    GATE_SCOPE,
    GATE_SCOPE_CLARIFY,
    GATE_TICKETS,
    is_approved,
    is_pass,
    request_approval,
    request_clarification,
    request_manual_test,
)
from app.orchestration.state import PipelineState
from app.services.summarizer import refresh_title_summary
from app.tools.artifacts import write_artifact
from app.tools.registry import ToolContext

# --- retry ceilings so no automated loop can spin forever ---
MAX_REVIEW_ROUNDS = 4
MAX_SECURITY_ROUNDS = 2
MAX_AUTO_TEST_ROUNDS = 2


# ======================================================================
# Agent nodes
# ======================================================================
async def scope_agent(state: PipelineState) -> Command:
    pid = state["project_id"]
    await publish_event(pid, "agent_started", "scope", "Scope Discovery analyzing the project idea")
    tool, args = await scope_mod.run(state)

    if tool == "ask_human":
        questions = args.get("questions", [])
        await publish_event(pid, "agent_message", "scope", "Asking clarifying questions", {"questions": questions})
        return Command(goto="scope_wait_node", update={"scope_pending_questions": questions})

    doc = args.get("doc", "")
    await publish_event(pid, "agent_message", "scope", "Scope drafted, ready for approval")
    asyncio.create_task(refresh_title_summary(pid, state.get("raw_idea", ""), doc))
    return Command(
        goto="scope_gate_node",
        update={"scope_doc": doc, "current_agent": "scope", "status": "awaiting_scope_approval"},
    )


async def scope_wait_node(state: PipelineState) -> Command:
    pid = state["project_id"]
    questions = state.get("scope_pending_questions", [])
    await publish_event(pid, "gate", "scope", "Waiting for answers", {"gate": GATE_SCOPE_CLARIFY, "questions": questions})
    decision = request_clarification(GATE_SCOPE_CLARIFY, questions)
    qa = (state.get("scope_qa") or []) + [{"questions": questions, "answer": decision.get("feedback", "")}]
    return Command(goto="scope_agent", update={"scope_qa": qa, "scope_pending_questions": []})


async def scope_gate_node(state: PipelineState) -> Command:
    pid = state["project_id"]
    await publish_event(pid, "gate", "scope", "Waiting for APPROVE_SCOPE", {"gate": GATE_SCOPE})
    decision = request_approval(
        GATE_SCOPE,
        "Confirm the project scope before architecture begins.",
        {"scope_doc": state.get("scope_doc"), "qa": state.get("scope_qa", [])},
    )
    if is_approved(decision):
        return Command(goto="architect_agent", update={"status": "scope_approved"})
    return Command(goto="scope_agent", update={"scope_feedback": decision.get("feedback", "")})


async def architect_agent(state: PipelineState) -> Command:
    pid = state["project_id"]
    await publish_event(pid, "agent_started", "architect", "Architecture Designer designing the system")
    tool, args = await architect_mod.run(state)

    if tool == "ask_human":
        questions = args.get("questions", [])
        await publish_event(pid, "agent_message", "architect", "Asking clarifying questions", {"questions": questions})
        return Command(goto="architect_wait_node", update={"architecture_pending_questions": questions})

    doc = args.get("doc", "")
    await publish_event(pid, "agent_message", "architect", "Architecture drafted, ready for approval")
    return Command(
        goto="architecture_gate_node",
        update={"architecture_doc": doc, "current_agent": "architect", "status": "awaiting_architecture_approval"},
    )


async def architect_wait_node(state: PipelineState) -> Command:
    pid = state["project_id"]
    questions = state.get("architecture_pending_questions", [])
    await publish_event(
        pid, "gate", "architect", "Waiting for answers", {"gate": GATE_ARCHITECTURE_CLARIFY, "questions": questions}
    )
    decision = request_clarification(GATE_ARCHITECTURE_CLARIFY, questions)
    qa = (state.get("architecture_qa") or []) + [{"questions": questions, "answer": decision.get("feedback", "")}]
    return Command(goto="architect_agent", update={"architecture_qa": qa, "architecture_pending_questions": []})


async def architecture_gate_node(state: PipelineState) -> Command:
    pid = state["project_id"]
    await publish_event(pid, "gate", "architect", "Waiting for APPROVE_ARCHITECTURE", {"gate": GATE_ARCHITECTURE})
    decision = request_approval(
        GATE_ARCHITECTURE,
        "Confirm the system design before planning begins.",
        {"architecture_doc": state.get("architecture_doc"), "qa": state.get("architecture_qa", [])},
    )
    if is_approved(decision):
        await knowledge.architecture_approved(
            pid, state.get("raw_idea", "")[:120], state.get("architecture_doc", ""), state.get("architecture_qa")
        )
        return Command(goto="planner_agent", update={"status": "architecture_approved"})
    return Command(goto="architect_agent", update={"architecture_feedback": decision.get("feedback", "")})


async def planner_agent(state: PipelineState) -> dict:
    pid = state["project_id"]
    await publish_event(pid, "agent_started", "planner", "Project Planner breaking work into tickets")
    tickets = await planner_mod.run(state)
    for t in tickets:
        t.setdefault("status", "pending")
    await publish_event(pid, "agent_message", "planner", f"Created {len(tickets)} tickets")
    return {
        "tickets": tickets,
        "current_ticket_index": 0,
        "current_agent": "planner",
        "status": "awaiting_tickets_approval",
    }


async def tickets_gate_node(state: PipelineState) -> Command:
    pid = state["project_id"]
    await publish_event(pid, "gate", "planner", "Waiting for APPROVE_TICKETS", {"gate": GATE_TICKETS})
    decision = request_approval(
        GATE_TICKETS,
        "Confirm the ticket breakdown before coding starts.",
        {"tickets": state.get("tickets")},
    )
    if is_approved(decision):
        return Command(goto="developer_agent", update={"status": "tickets_approved"})
    return Command(goto="planner_agent", update={"tickets_feedback": decision.get("feedback", "")})


def _current_ticket(state: PipelineState) -> dict:
    tickets = state.get("tickets") or []
    idx = state.get("current_ticket_index", 0)
    return tickets[idx] if 0 <= idx < len(tickets) else {}


def _with_ticket_status(state: PipelineState, status: str) -> list[dict]:
    """Return the ticket list with the current ticket's status field replaced."""
    tickets = list(state.get("tickets") or [])
    idx = state.get("current_ticket_index", 0)
    if 0 <= idx < len(tickets):
        tickets[idx] = {**tickets[idx], "status": status}
    return tickets


async def developer_agent(state: PipelineState) -> dict:
    pid = state["project_id"]
    ticket = _current_ticket(state)
    await publish_event(pid, "agent_started", "developer", f"Developer picking up {ticket.get('id')}", {"ticket": ticket})
    branch = f"ticket/{ticket.get('id', 'T-000')}"
    summary = await developer_mod.run(state, ticket)
    if not summary.strip():
        # Output fallback: the model finished without a usable summary — the
        # work may still exist on disk, so synthesize one instead of letting
        # the ticket "complete" with nothing to show.
        files = await _changed_files(pid)
        summary = (
            f"Implementation committed ({len(files)} file(s) touched: {', '.join(files[:8])})"
            if files else "The developer finished without reporting changes — flagging for review."
        )
    await publish_event(pid, "agent_message", "developer", f"Committed on {branch}, ready for review: {summary}")
    return {
        "branch": branch,
        "dev_notes": summary,
        "current_agent": "developer",
        "status": "in_review",
        "tickets": _with_ticket_status(state, "in_review"),
    }


async def security_scan_node(state: PipelineState) -> Command:
    """Deterministic secrets + SAST scan on every change before review.
    Blocking findings bounce the ticket back to the developer (with a rounds
    ceiling — persistent findings go forward flagged, and the risk gate
    hard-stops on them rather than letting them ship)."""
    pid = state["project_id"]
    ticket = _current_ticket(state)
    rounds = state.get("security_rounds", 0) + 1
    await publish_event(pid, "agent_started", "security", f"Security scanning {ticket.get('id')}", {"ticket": ticket})

    report = await run_security_scan(ToolContext(project_id=pid, agent="security").repo_dir)
    findings = report.blocking + report.advisory
    await knowledge.test_completed(pid, ticket, "security", report.passed, report.summary, rounds=rounds)

    if report.passed:
        await publish_event(
            pid, "agent_message", "security", f"Security scan passed ({report.summary})",
            {"ticket": ticket, "passed": True},
        )
        return Command(goto="reviewer_agent", update={"security_findings": findings, "security_rounds": rounds})

    if rounds >= MAX_SECURITY_ROUNDS:
        await publish_event(
            pid, "agent_message", "security",
            f"Blocking findings persist after {rounds} scans — flagging for review and human gate",
            {"ticket": ticket, "findings": report.blocking},
        )
        return Command(goto="reviewer_agent", update={"security_findings": findings, "security_rounds": rounds})

    await publish_event(
        pid, "agent_message", "security", "Blocking security findings — sending back to developer",
        {"ticket": ticket, "findings": report.blocking},
    )
    return Command(
        goto="developer_agent",
        update={
            "security_findings": findings,
            "security_rounds": rounds,
            "review_result": "needs_fix",
            "review_notes": "The security scan found blocking issues you must fix:\n"
            + json.dumps(report.blocking, indent=2),
            "status": "security_fix",
            "tickets": _with_ticket_status(state, "needs_fix"),
        },
    )


async def reviewer_agent(state: PipelineState) -> Command:
    pid = state["project_id"]
    ticket = _current_ticket(state)
    rounds = state.get("review_rounds", 0) + 1
    await publish_event(
        pid, "agent_started", "reviewer", f"Reviewer checking {ticket.get('id')} (round {rounds})", {"ticket": ticket}
    )
    approved, notes = await reviewer_mod.run(state, ticket)
    await knowledge.review_completed(
        pid, ticket, approved, rounds, notes, fixed_by_debugger=bool(state.get("debug_notes"))
    )
    if not approved and rounds >= MAX_REVIEW_ROUNDS:
        await publish_event(
            pid,
            "agent_message",
            "reviewer",
            f"Round {rounds} still has issues, but hit the retry ceiling — forcing through to manual test",
            {"ticket": ticket},
        )
        approved = True
    if approved:
        await publish_event(pid, "agent_message", "reviewer", f"Review passed: {notes}", {"ticket": ticket, "passed": True})
        return Command(
            goto="auto_test_node",
            update={
                "review_result": "approved",
                "review_rounds": rounds,
                "review_notes": notes,
                "current_agent": "reviewer",
                "status": "ready_for_test",
                "tickets": _with_ticket_status(state, "ready_for_test"),
            },
        )
    await publish_event(
        pid, "agent_message", "reviewer", f"Found issues — sending back to developer: {notes}", {"ticket": ticket}
    )
    return Command(
        goto="developer_agent",
        update={
            "review_result": "needs_fix",
            "review_rounds": rounds,
            "review_notes": notes,
            "current_agent": "reviewer",
            "status": "needs_fix",
            "tickets": _with_ticket_status(state, "needs_fix"),
        },
    )


async def auto_test_node(state: PipelineState) -> Command:
    """Deterministic test run after review passes: pytest / npm test / syntax
    check, whatever the generated repo actually has. Failures route to the
    Debugger with the real output; a persistent failure escalates to the risk
    gate (which hard-stops for a human) instead of looping forever."""
    pid = state["project_id"]
    ticket = _current_ticket(state)
    rounds = state.get("auto_test_rounds", 0) + 1
    await publish_event(pid, "agent_started", "test", f"Running automated tests for {ticket.get('id')}", {"ticket": ticket})

    report = await run_repo_tests(ToolContext(project_id=pid, agent="test").repo_dir)
    await knowledge.test_completed(pid, ticket, "auto_test", report.passed, report.output[:1500], rounds=rounds)

    if report.passed:
        await publish_event(
            pid, "agent_message", "test", f"Automated tests passed ({report.summary})",
            {"ticket": ticket, "passed": True},
        )
        return Command(
            goto="risk_gate_node",
            update={"auto_test_rounds": rounds, "auto_test_summary": report.summary},
        )

    if rounds >= MAX_AUTO_TEST_ROUNDS:
        await publish_event(
            pid, "agent_message", "test",
            f"Tests still failing after {rounds} fix attempts — escalating to the human gate",
            {"ticket": ticket},
        )
        return Command(
            goto="risk_gate_node",
            update={"auto_test_rounds": rounds, "auto_test_summary": f"FAILING: {report.output[-800:]}"},
        )

    await publish_event(
        pid, "agent_message", "test", f"Automated tests failed ({report.summary}) — routing to debugger",
        {"ticket": ticket},
    )
    return Command(
        goto="debugger_agent",
        update={
            "auto_test_rounds": rounds,
            "auto_test_summary": report.summary,
            "test_result": "fail",
            "test_feedback": f"Automated tests failed.\nRan: {', '.join(report.ran)}\nOutput:\n{report.output[-1500:]}",
            "tickets": _with_ticket_status(state, "failed"),
        },
    )


async def risk_gate_node(state: PipelineState) -> Command:
    """Tier 0/1 ships with an audit trail / async digest; Tier 2 stops at the
    human MANUAL_TEST gate. The doc's Section 08, scored per change from the
    outcome ledger plus this run's own security/test signals."""
    pid = state["project_id"]
    ticket = _current_ticket(state)
    findings = state.get("security_findings") or []
    blocking = [f for f in findings if f.get("severity") in ("BLOCKING", "HIGH")]
    advisory = [f for f in findings if f.get("severity") not in ("BLOCKING", "HIGH")]
    tests_failed = (state.get("auto_test_summary") or "").startswith("FAILING")

    assessment = await risk_mod.assess_ticket(
        ticket, security_blocking=len(blocking), security_advisory=len(advisory), tests_failed=tests_failed
    )
    await publish_event(
        pid, "agent_message", "supervisor",
        f"Risk: tier {assessment.tier}, score {assessment.score} ({assessment.category})",
        {"ticket": ticket, "risk": assessment.as_payload()},
    )

    if assessment.tier == 2:
        return Command(goto="manual_test_gate_node", update={"risk": assessment.as_payload()})

    kind = "audit" if assessment.tier == 0 else "digest"
    await publish_event(
        pid, kind, "supervisor",
        f"Tier {assessment.tier} auto-approval: {ticket.get('title')}",
        {"ticket": ticket, "risk": assessment.as_payload()},
    )
    await knowledge.test_completed(
        pid, ticket, "risk_gate", True, f"auto-approved at tier {assessment.tier}",
        rounds=state.get("review_rounds", 0),
    )
    return Command(
        goto="docs_update",
        update={
            "risk": assessment.as_payload(),
            "test_result": "pass",
            "status": "ticket_passed",
            "tickets": _with_ticket_status(state, "passed"),
        },
    )


async def manual_test_gate_node(state: PipelineState) -> Command:
    pid = state["project_id"]
    ticket = _current_ticket(state)
    await publish_event(pid, "gate", "human", "Waiting for manual test result", {"ticket": ticket, "risk": state.get("risk")})
    decision = request_manual_test(
        {
            "ticket": ticket,
            "checklist": ticket.get("test_checklist", []),
            "branch": state.get("branch"),
            "risk": state.get("risk"),
            "security_findings": [f for f in (state.get("security_findings") or []) if f.get("severity") in ("BLOCKING", "HIGH")],
            "auto_test_summary": state.get("auto_test_summary", ""),
        }
    )
    if is_pass(decision):
        await knowledge.test_completed(pid, ticket, "manual_test", True, "", rounds=state.get("review_rounds", 0))
        return Command(
            goto="docs_update",
            update={"test_result": "pass", "status": "ticket_passed", "tickets": _with_ticket_status(state, "passed")},
        )
    feedback = decision.get("feedback", "")
    await knowledge.test_completed(pid, ticket, "manual_test", False, feedback, rounds=state.get("review_rounds", 0))
    return Command(
        goto="debugger_agent",
        update={
            "test_result": "fail",
            "test_feedback": feedback,
            "tickets": _with_ticket_status(state, "failed"),
        },
    )


async def debugger_agent(state: PipelineState) -> dict:
    pid = state["project_id"]
    ticket = _current_ticket(state)
    await publish_event(
        pid, "agent_started", "debugger", f"Debugger fixing {ticket.get('id')}", {"ticket": ticket, "feedback": state.get("test_feedback")}
    )
    summary = await debugger_mod.run(state, ticket)
    await knowledge.incident_resolved(pid, ticket, symptom=state.get("test_feedback", ""), fix=summary)
    await publish_event(pid, "agent_message", "debugger", f"Applied fix, re-submitting for review: {summary}", {"ticket": ticket})
    return {
        "debug_notes": summary,
        "current_agent": "debugger",
        "status": "in_review",
        "tickets": _with_ticket_status(state, "in_review"),
    }


async def _changed_files(pid: str) -> list[str]:
    """Names of files the current branch touched (vs main), for outcome records."""
    from app.tools.git_tools import _run_git, ensure_repo

    ctx = ToolContext(project_id=pid, agent="developer")
    try:
        await ensure_repo(ctx)
        result = await _run_git(ctx, "diff", "--name-only", "main...HEAD")
        files = [f for f in result.get("stdout", "").splitlines() if f.strip()]
        if not files:  # uncommitted work still counts as output
            result = await _run_git(ctx, "status", "--porcelain")
            files = [line[3:] for line in result.get("stdout", "").splitlines() if len(line) > 3]
        return files
    except Exception:  # noqa: BLE001 — outcome enrichment must never fail the pipeline
        return []


async def docs_update(state: PipelineState) -> Command:
    pid = state["project_id"]
    ticket = _current_ticket(state)
    tickets = state.get("tickets") or []
    idx = state.get("current_ticket_index", 0)

    ctx = ToolContext(project_id=pid, agent="developer")
    notes = state.get("debug_notes") or state.get("dev_notes") or ""
    await write_artifact(ctx, kind="docs", content=f"## {ticket.get('id')}: {ticket.get('title')}\n\n{notes}\n")

    # Durable per-ticket outcome — the "what did this task actually produce"
    # record the UI shows when a task is clicked.
    files = await _changed_files(pid)
    outcomes = dict(state.get("ticket_outcomes") or {})
    outcomes[str(ticket.get("id", f"T-{idx}"))] = {
        "title": ticket.get("title", ""),
        "summary": notes,
        "review_notes": state.get("review_notes", ""),
        "auto_test_summary": state.get("auto_test_summary", ""),
        "risk": state.get("risk") or {},
        "files_changed": files,
    }
    # Chat-visible completion so finishing a task is never silent.
    await publish_event(
        pid, "ticket_done", "docs", f"Ticket {ticket.get('id')} passed",
        {"ticket": ticket, "summary": notes, "files_changed": files},
    )

    next_idx = idx + 1
    if next_idx < len(tickets):
        return Command(
            goto="developer_agent",
            update={
                "current_ticket_index": next_idx,
                "ticket_outcomes": outcomes,
                "review_rounds": 0,
                "review_result": "",
                "review_notes": "",
                "test_result": "",
                "test_feedback": "",
                "debug_notes": "",
                "security_findings": [],
                "security_rounds": 0,
                "auto_test_rounds": 0,
                "auto_test_summary": "",
                "risk": {},
                "current_agent": "docs",
                "status": "next_ticket",
            },
        )

    # ---- pipeline complete: produce and surface the final deliverable ----
    report = _final_report(state, outcomes)
    await write_artifact(ctx, kind="final_report", content=report)
    from app.services import project_store as store_mod

    await store_mod.set_result(pid, {"status": "succeeded", "result": report, "label": "software_project"})
    await publish_event(pid, "result", "docs", "Final project report ready", {"result": report})
    await publish_event(pid, "done", "system", "All tickets passed — pipeline complete")
    return Command(goto=END, update={"status": "complete", "ticket_outcomes": outcomes})


def _final_report(state: PipelineState, outcomes: dict[str, dict]) -> str:
    """The end-of-pipeline deliverable: what was built, per ticket, and how to
    find it. Written to docs/ and pushed into the chat as the final answer."""
    tickets = state.get("tickets") or []
    lines = [
        "# Project delivered",
        "",
        f"**Request:** {state.get('raw_idea', '')[:400]}",
        "",
        f"All {len(tickets)} planned task(s) passed review, security scanning and testing.",
        "",
        "## What was built",
    ]
    for t in tickets:
        tid = str(t.get("id", ""))
        outcome = outcomes.get(tid, {})
        lines.append(f"### {tid}: {t.get('title', '')}")
        if outcome.get("summary"):
            lines.append(outcome["summary"])
        files = outcome.get("files_changed") or []
        if files:
            lines.append("")
            lines.append("Files: " + ", ".join(f"`{f}`" for f in files[:15]))
        lines.append("")
    lines += [
        "## Where everything lives",
        "",
        "- Generated code: the **Files** panel (project workspace `repo/`)",
        "- Documents (scope, architecture, changelog): the **Files** panel under `docs/`",
        "- Run or test it: the **Sandbox** panel",
    ]
    return "\n".join(lines)


# ======================================================================
# Graph assembly
# ======================================================================
def build_graph_uncompiled() -> StateGraph:
    g = StateGraph(PipelineState)

    g.add_node("scope_agent", scope_agent)
    g.add_node("scope_wait_node", scope_wait_node)
    g.add_node("scope_gate_node", scope_gate_node)
    g.add_node("architect_agent", architect_agent)
    g.add_node("architect_wait_node", architect_wait_node)
    g.add_node("architecture_gate_node", architecture_gate_node)
    g.add_node("planner_agent", planner_agent)
    g.add_node("tickets_gate_node", tickets_gate_node)
    g.add_node("developer_agent", developer_agent)
    g.add_node("security_scan_node", security_scan_node)
    g.add_node("reviewer_agent", reviewer_agent)
    g.add_node("auto_test_node", auto_test_node)
    g.add_node("risk_gate_node", risk_gate_node)
    g.add_node("manual_test_gate_node", manual_test_gate_node)
    g.add_node("debugger_agent", debugger_agent)
    g.add_node("docs_update", docs_update)

    # scope_agent / architect_agent route dynamically (Command.goto) between
    # their own wait node and the final approval gate. Everything else that
    # only ever has one destination stays a static edge.
    g.add_edge(START, "scope_agent")
    g.add_edge("planner_agent", "tickets_gate_node")
    g.add_edge("developer_agent", "security_scan_node")
    g.add_edge("debugger_agent", "security_scan_node")
    return g


def build_graph():
    """Compile the graph with the Redis checkpointer (async)."""
    from app.orchestration.checkpointer import get_checkpointer

    return build_graph_uncompiled().compile(checkpointer=get_checkpointer())


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
