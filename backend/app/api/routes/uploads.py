"""Attachment upload endpoint.

Files are staged (not tied to a project yet) and their text extracted up front,
so the home screen can attach-then-submit in one flow. The run endpoints
(`/projects`, `/projects/{pid}/message`) reference the returned `batch_id` and
inject the extracted text as context.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import get_settings
from app.services import attachments

router = APIRouter(tags=["uploads"])


@router.post("/uploads")
async def upload(files: list[UploadFile] = File(...)) -> dict:
    settings = get_settings()
    if not files:
        raise HTTPException(status_code=400, detail="no files provided")
    if len(files) > settings.max_attachments_per_batch:
        raise HTTPException(status_code=400, detail=f"too many files (max {settings.max_attachments_per_batch})")

    payloads: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in attachments.ALLOWED_EXT:
            raise HTTPException(status_code=415, detail=f"unsupported file type: {ext or '(none)'}")
        data = await f.read()
        if len(data) > settings.max_attachment_bytes:
            raise HTTPException(status_code=413, detail=f"{f.filename} is too large")
        total += len(data)
        if total > settings.max_attachment_batch_bytes:
            raise HTTPException(status_code=413, detail="attachments exceed the batch size limit")
        payloads.append((f.filename or "file", data))

    batch_id, metas = await attachments.stage_batch(payloads)
    return {"batch_id": batch_id, "files": attachments.as_meta_dicts(metas)}
