"""System-control REST: resolve an action (no execution) and record audit.

The conversational path goes through /projects/{pid}/message and rides the SSE
stream as a ui_action event. These endpoints support a direct resolve preview
and let the client report what actually happened (executed/confirmed/cancelled)
so the audit trail is complete.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.control import actions

router = APIRouter(tags=["control"])

USER = "local"


class ResolveBody(BaseModel):
    text: str


class AuditBody(BaseModel):
    audit_id: str = ""
    phase: str            # executed | confirmed | cancelled | denied
    action: str = ""
    detail: dict = {}


@router.post("/control/resolve")
async def resolve(body: ResolveBody) -> dict:
    resolved = await actions.resolve_smart(body.text)
    return {"resolved": resolved.as_payload() if resolved else None}


@router.post("/control/audit")
async def record_audit(body: AuditBody) -> dict:
    aid = await actions.audit(USER, body.phase, body.action, body.detail, audit_id=body.audit_id or None)
    return {"ok": True, "audit_id": aid}


@router.get("/control/audit")
async def get_audit(limit: int = 100) -> dict:
    return {"entries": await actions.audit_log(USER, min(max(limit, 1), 500))}
