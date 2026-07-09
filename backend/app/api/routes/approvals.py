"""Human decision endpoints: approve/reject a gate, or PASS/FAIL a manual test.

Both resume the paused graph via the runner.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import ApprovalRequest, ManualTestRequest
from app.orchestration import runner
from app.services import project_store as store

router = APIRouter(tags=["approvals"])


async def _require_pending(pid: str, interrupt_id: str = "") -> dict:
    if await store.get_project(pid) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if await store.is_running(pid):
        raise HTTPException(status_code=409, detail="pipeline is busy; wait for the current step")
    pending = await store.get_pending(pid)
    if pending is None:
        raise HTTPException(status_code=409, detail="no gate is currently awaiting input")
    # A stale client answering yesterday's gate must get a clear 409, not
    # silently resolve whichever gate happens to be pending now.
    if interrupt_id and pending.get("interrupt_id") and interrupt_id != pending.get("interrupt_id"):
        raise HTTPException(status_code=409, detail="this gate has already been resolved — refresh to see the current one")
    return pending


@router.post("/projects/{pid}/approve")
async def approve(pid: str, req: ApprovalRequest) -> dict:
    pending = await _require_pending(pid, req.interrupt_id)
    if pending.get("kind") != "approval":
        raise HTTPException(status_code=409, detail=f"current gate '{pending.get('gate')}' is not an approval gate")
    await runner.resume(pid, {"action": req.action, "feedback": req.feedback})
    return {"ok": True, "gate": pending.get("gate"), "action": req.action}


@router.post("/projects/{pid}/test")
async def manual_test(pid: str, req: ManualTestRequest) -> dict:
    pending = await _require_pending(pid, req.interrupt_id)
    if pending.get("kind") != "manual_test":
        raise HTTPException(status_code=409, detail="current gate is not the manual-test gate")
    await runner.resume(pid, {"result": req.result, "feedback": req.feedback})
    return {"ok": True, "gate": pending.get("gate"), "result": req.result}
