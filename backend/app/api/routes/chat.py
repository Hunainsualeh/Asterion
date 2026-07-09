"""Conversation endpoints: mid-stage clarifying answers and free-form
follow-up messages on an existing project."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.dag import task_runner
from app.models.schemas import AnswerRequest, MessageRequest
from app.orchestration import runner
from app.orchestration.events import publish_event
from app.orchestration.intent import Intent, classify
from app.services import attachments
from app.services import project_store as store
from app.tools.registry import ToolContext

router = APIRouter(tags=["chat"])


@router.post("/projects/{pid}/answer")
async def answer(pid: str, req: AnswerRequest) -> dict:
    if await store.get_project(pid) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if await store.is_running(pid):
        raise HTTPException(status_code=409, detail="pipeline is busy; wait for the current step")
    pending = await store.get_pending(pid)
    if pending is None:
        raise HTTPException(status_code=409, detail="no gate is currently awaiting input")
    if req.interrupt_id and pending.get("interrupt_id") and req.interrupt_id != pending.get("interrupt_id"):
        raise HTTPException(status_code=409, detail="this question has already been answered — refresh to see the current one")
    if pending.get("kind") != "clarify":
        raise HTTPException(status_code=409, detail=f"current gate '{pending.get('gate')}' is not awaiting an answer")
    await runner.resume(pid, {"feedback": req.feedback})
    return {"ok": True, "gate": pending.get("gate")}


@router.post("/projects/{pid}/message")
async def send_message(pid: str, req: MessageRequest) -> dict:
    """Free-form follow-up on an existing project: classify it, echo it into
    the chat stream as a user bubble, and run it in the task lane with the
    conversation so far as context."""
    if await store.get_project(pid) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if await store.is_running(pid):
        raise HTTPException(status_code=409, detail="still working on the last request — stop it or wait")
    if await store.get_pending(pid) is not None:
        raise HTTPException(status_code=409, detail="a question is awaiting your answer — reply to that first")
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="empty message")

    # Context is everything BEFORE this message, so the prompt's
    # "conversation so far" doesn't duplicate the new request.
    history = await store.get_history(pid, limit=10)
    context = "\n\n".join(f"{h.get('role', '?')}: {h.get('text', '')}" for h in history)

    await store.append_history(pid, "user", message)
    await publish_event(pid, "user_message", "user", message)

    deep_research = req.mode == "research"

    # If we just asked clarifying questions, this message answers them: merge
    # it with the original request and build — never interrogate twice.
    pending_clarify = await store.get_clarify(pid)
    if deep_research:
        run_query = message
        intent = Intent(kind="research", complexity="complex", confidence=1.0,
                        reason="deep research mode", source="mode")
    elif pending_clarify:
        await store.clear_clarify(pid)
        run_query = f"{pending_clarify.get('query', '')}\n\nUser's answers/details: {message}"
        intent = await classify(run_query)
        intent.questions = []
    else:
        run_query = message
        intent = await classify(message)
    intent.normalized()

    # Assistant-platform lanes: a task/reminder command goes to the Task Agent
    # (its own datastore, independent of this chat's memory); an app-control
    # command goes to the action resolver. Both ride this project's SSE stream.
    if not deep_research and intent.kind == "task_command":
        from app.tasks import agent as task_agent

        await store.set_lane(pid, "task", intent=intent.as_payload())
        await task_agent.run(pid, run_query, tz=req.timezone, context=context)
        return {"ok": True, "lane": "task", "intent": intent.as_payload()}
    if not deep_research and intent.kind == "system_control":
        from app.control import service as control_service

        await store.set_lane(pid, "task", intent=intent.as_payload())
        await control_service.run(pid, run_query)
        return {"ok": True, "lane": "task", "intent": intent.as_payload()}

    # Ground the run in any uploaded documents.
    if req.attachment_batch_id:
        docs_dir = ToolContext(project_id=pid, agent="orchestrator").docs_dir
        run_query = attachments.augment_query(run_query, attachments.consume(req.attachment_batch_id, docs_dir))
    if req.tone:
        run_query = f"[Response style: {req.tone}]\n\n{run_query}"

    if deep_research:
        await store.set_lane(pid, "task", intent=intent.as_payload())
        await task_runner.start(pid, run_query, intent, context=context, deep_research=True)
        return {"ok": True, "lane": "task", "intent": intent.as_payload()}

    if intent.kind == "software_project":
        # A genuinely complex build request mid-conversation escalates this
        # project to the full SDLC pipeline (scope → architecture → tickets →
        # build/review/test, with approval gates) instead of a chat answer.
        await store.set_lane(pid, "project", intent=intent.as_payload())
        await runner.start(pid, run_query)
        return {"ok": True, "lane": "project", "intent": intent.as_payload()}

    await task_runner.start(pid, run_query, intent, context=context)
    return {"ok": True, "lane": "task", "intent": intent.as_payload()}
