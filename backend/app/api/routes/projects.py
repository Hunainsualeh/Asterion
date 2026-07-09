"""Project lifecycle endpoints: start (with intent routing), list, inspect,
cancel, retry, plus per-project DAG state and metrics."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException

from app.dag import engine as dag_engine
from app.dag import task_runner
from app.models.schemas import (
    ProjectDetail,
    ProjectSummary,
    RenameRequest,
    StartProjectRequest,
    StartProjectResponse,
)
from app.observability import get_global_metrics, get_project_metrics
from app.orchestration import runner
from app.orchestration.intent import Intent, classify
from app.services import attachments
from app.services import project_store as store
from app.tools.registry import ToolContext

router = APIRouter(tags=["projects"])


def _stored_intent(proj: dict) -> dict:
    try:
        return json.loads(proj.get("intent") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


@router.post("/projects", response_model=StartProjectResponse)
async def start_project(req: StartProjectRequest) -> StartProjectResponse:
    pid = f"proj-{uuid.uuid4().hex[:12]}"
    deep_research = req.mode == "research"

    # Route BEFORE any orchestration: Deep Research forces the multi-step
    # research flow; otherwise conversation gets a direct answer, simple/moderate
    # dev tasks get the task lane, and a genuinely complex build wakes the SDLC.
    if deep_research:
        intent = Intent(kind="research", complexity="complex", confidence=1.0,
                        reason="deep research mode", source="mode")
    elif req.lane == "auto":
        intent = (await classify(req.idea)).normalized()
    elif req.lane == "project":
        intent = Intent(kind="software_project", complexity="complex", confidence=1.0,
                        reason="forced by caller", source="override")
    else:
        intent = await classify(req.idea)
        if intent.kind == "software_project":  # honor the task override
            intent.kind = "coding"
        intent.normalized()

    lane = "task" if deep_research else intent.lane
    await store.create_project(pid, req.idea, lane=lane, intent=intent.as_payload())
    await store.append_history(pid, "user", req.idea)

    # Assistant-platform lanes: a task/reminder command or an app-control
    # command typed on the home screen creates a lightweight chat that just
    # confirms the action, rather than waking a build lane.
    if not deep_research and intent.kind == "task_command":
        from app.tasks import agent as task_agent

        await task_agent.run(pid, req.idea, tz=req.timezone)
        return StartProjectResponse(project_id=pid, status="starting", lane="task", intent=intent.as_payload())
    if not deep_research and intent.kind == "system_control":
        from app.control import service as control_service

        await control_service.run(pid, req.idea)
        return StartProjectResponse(project_id=pid, status="starting", lane="task", intent=intent.as_payload())

    # Ground the run in any uploaded documents (extracted text is prepended to
    # the query; the display idea stays the user's own words).
    run_query = req.idea
    if req.attachment_batch_id:
        docs_dir = ToolContext(project_id=pid, agent="orchestrator").docs_dir
        run_query = attachments.augment_query(req.idea, attachments.consume(req.attachment_batch_id, docs_dir))
    if req.tone:
        run_query = f"[Response style: {req.tone}]\n\n{run_query}"

    if deep_research:
        await task_runner.start(pid, run_query, intent, deep_research=True)
    elif lane == "project":
        await runner.start(pid, run_query)
    else:
        await task_runner.start(pid, run_query, intent)
    return StartProjectResponse(project_id=pid, status="starting", lane=lane, intent=intent.as_payload())


@router.get("/projects", response_model=list[ProjectSummary])
async def list_projects() -> list[ProjectSummary]:
    projects = await store.list_projects()
    summaries: list[ProjectSummary] = []
    for p in projects:
        pid = p["project_id"]
        pending = await store.get_pending(pid)
        stage = await store.get_stage(pid)
        summaries.append(
            ProjectSummary(
                project_id=pid,
                idea=p.get("idea", ""),
                title=p.get("title") or p.get("idea", ""),
                summary=p.get("summary", ""),
                status=p.get("status", ""),
                lane=p.get("lane", "project"),
                intent=_stored_intent(p),
                pending_gate=(pending or {}).get("gate"),
                running=p.get("running", False),
                stage=stage,
            )
        )
    return summaries


@router.post("/projects/{pid}/retry")
async def retry_project(pid: str) -> dict:
    proj = await store.get_project(pid)
    if proj is None:
        raise HTTPException(status_code=404, detail="project not found")
    if await store.is_running(pid):
        raise HTTPException(status_code=409, detail="pipeline is busy; wait for the current step")
    if proj.get("status") not in ("error", "cancelled"):
        raise HTTPException(status_code=409, detail="project isn't in an error state")
    if proj.get("lane", "project") == "task":
        intent = Intent(**{k: v for k, v in _stored_intent(proj).items()
                           if k in ("kind", "complexity", "confidence", "reason", "slots", "source")})
        await task_runner.start(pid, proj.get("idea", ""), intent)
    else:
        await runner.retry(pid)
    return {"ok": True}


@router.post("/projects/{pid}/cancel")
async def cancel_project(pid: str) -> dict:
    proj = await store.get_project(pid)
    if proj is None:
        raise HTTPException(status_code=404, detail="project not found")
    stopped = task_runner.cancel(pid) or runner.cancel(pid)
    if not stopped:
        raise HTTPException(status_code=409, detail="nothing is currently running for this project")
    return {"ok": True}


@router.patch("/projects/{pid}")
async def rename_project(pid: str, req: RenameRequest) -> dict:
    if await store.get_project(pid) is None:
        raise HTTPException(status_code=404, detail="project not found")
    await store.rename_project(pid, req.title)
    return {"ok": True, "title": req.title.strip()[:120]}


@router.delete("/projects")
async def delete_all_projects() -> dict:
    """Bulk delete: stop and drop every project. Backs the conversational
    'delete all chats' control action (and a future 'Clear all' UI button)."""
    projects = await store.list_projects()
    deleted = 0
    for p in projects:
        pid = p["project_id"]
        task_runner.cancel(pid)
        runner.cancel(pid)
        await store.delete_project(pid)
        deleted += 1
    return {"ok": True, "deleted": deleted}


@router.delete("/projects/{pid}")
async def delete_project(pid: str) -> dict:
    if await store.get_project(pid) is None:
        raise HTTPException(status_code=404, detail="project not found")
    # Stop anything in flight before dropping its state.
    task_runner.cancel(pid)
    runner.cancel(pid)
    await store.delete_project(pid)
    return {"ok": True}


@router.get("/projects/{pid}/dag")
async def get_dag(pid: str) -> dict:
    """Live DAG state (if running) plus persisted execution history."""
    if await store.get_project(pid) is None:
        raise HTTPException(status_code=404, detail="project not found")
    live = task_runner.active_run(pid)
    runs = await dag_engine.list_runs(pid)
    return {"live": live.snapshot() if live else None, "runs": runs}


@router.get("/projects/{pid}/result")
async def get_result(pid: str) -> dict:
    if await store.get_project(pid) is None:
        raise HTTPException(status_code=404, detail="project not found")
    result = await store.get_result(pid)
    return {"result": result}


@router.get("/projects/{pid}/metrics")
async def project_metrics(pid: str) -> dict:
    if await store.get_project(pid) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return await get_project_metrics(pid)


@router.get("/metrics")
async def global_metrics() -> dict:
    return await get_global_metrics()


@router.get("/projects/{pid}", response_model=ProjectDetail)
async def get_project(pid: str) -> ProjectDetail:
    proj = await store.get_project(pid)
    if proj is None:
        raise HTTPException(status_code=404, detail="project not found")

    pending = await store.get_pending(pid)
    running = await store.is_running(pid)
    stage = await store.get_stage(pid)
    result = await store.get_result(pid)
    lane = proj.get("lane", "project")

    state_view: dict = {}
    if lane == "project":
        snap = await runner.snapshot(pid)
        values = snap.values if snap else {}
        # Trim large/binary-ish fields from the summary state.
        state_view = {
            "status": values.get("status"),
            "current_agent": values.get("current_agent"),
            "scope_doc": values.get("scope_doc"),
            "scope_qa": values.get("scope_qa"),
            "architecture_doc": values.get("architecture_doc"),
            "architecture_qa": values.get("architecture_qa"),
            "tickets": values.get("tickets"),
            "current_ticket_index": values.get("current_ticket_index"),
            "ticket_outcomes": values.get("ticket_outcomes"),
            "review_result": values.get("review_result"),
            "review_notes": values.get("review_notes"),
            "dev_notes": values.get("dev_notes"),
            "debug_notes": values.get("debug_notes"),
            "test_result": values.get("test_result"),
            "test_feedback": values.get("test_feedback"),
            "branch": values.get("branch"),
            "risk": values.get("risk"),
            "auto_test_summary": values.get("auto_test_summary"),
            "security_findings": values.get("security_findings"),
        }

    live = task_runner.active_run(pid)
    dag_view = live.snapshot() if live else None
    if dag_view is None and lane == "task":
        runs = await dag_engine.list_runs(pid)
        dag_view = runs[0] if runs else None

    return ProjectDetail(
        project_id=pid,
        idea=proj.get("idea", ""),
        title=proj.get("title") or proj.get("idea", ""),
        summary=proj.get("summary", ""),
        status=proj.get("status", ""),
        lane=lane,
        intent=_stored_intent(proj),
        pending_gate=(pending or {}).get("gate"),
        running=running,
        stage=stage,
        interrupt=pending,
        state={k: v for k, v in state_view.items() if v is not None},
        result=result,
        dag=dag_view,
    )
