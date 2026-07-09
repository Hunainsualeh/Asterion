"""Artifact browsing API — makes everything the agents produce visible.

The pipeline writes documents to `workspace/<pid>/docs/` and code to
`workspace/<pid>/repo/`; until this router existed there was no way to read
any of it over HTTP, which is why "completed" tasks looked empty in the UI.

All paths are resolved against the project's workspace root and verified to
stay inside it (same containment rule as the agents' own fs tools).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.config import get_settings
from app.models.schemas import FileWriteRequest
from app.services import project_store as store

router = APIRouter(tags=["artifacts"])

MAX_FILE_BYTES = 400_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".next", ".pytest_cache"}
MAX_ENTRIES = 500


def _workspace(pid: str) -> Path:
    return get_settings().workspace_dir / pid


def _resolve(pid: str, rel_path: str) -> Path:
    root = _workspace(pid).resolve()
    candidate = (root / rel_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(status_code=400, detail="path escapes the project workspace")
    return candidate


async def _require_project(pid: str) -> None:
    # Disk-backed check: the workspace files outlive the volatile store, so a
    # restart that drops the fakeredis metadata must not hide the files.
    if not await store.project_exists(pid):
        raise HTTPException(status_code=404, detail="project not found")


def _tree(root: Path) -> list[dict]:
    """Flat, sorted listing of files under root (bounded, junk dirs skipped)."""
    entries: list[dict] = []
    if not root.exists():
        return entries
    for path in sorted(root.rglob("*")):
        if len(entries) >= MAX_ENTRIES:
            break
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.is_file():
            entries.append({
                "path": str(rel).replace("\\", "/"),
                "size": path.stat().st_size,
                "modified": path.stat().st_mtime,
            })
    return entries


@router.get("/projects/{pid}/artifacts")
async def list_artifacts(pid: str) -> dict:
    """Everything the agents produced: docs (scope, architecture, results,
    per-step outputs) and the generated repo's file tree."""
    await _require_project(pid)
    ws = _workspace(pid)
    return {
        "docs": _tree(ws / "docs"),
        "repo": _tree(ws / "repo"),
    }


@router.get("/projects/{pid}/artifacts/content")
async def read_artifact(pid: str, path: str = Query(..., min_length=1), root: str = Query("repo")) -> dict:
    await _require_project(pid)
    if root not in ("repo", "docs"):
        raise HTTPException(status_code=400, detail="root must be 'repo' or 'docs'")
    target = _resolve(pid, f"{root}/{path}")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"no such file: {path}")
    data = target.read_bytes()
    truncated = len(data) > MAX_FILE_BYTES
    try:
        content = data[:MAX_FILE_BYTES].decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        content = ""
        binary = True
    return {"path": path, "root": root, "content": content, "truncated": truncated,
            "binary": binary, "size": len(data)}


@router.get("/projects/{pid}/artifacts/raw/{path:path}")
async def raw_artifact(pid: str, path: str, root: str = Query("repo")) -> FileResponse:
    """Serve a workspace file as-is with its real MIME type — powers the HTML
    preview iframe. Relative asset URLs inside a previewed page (style.css,
    script.js) resolve back to this same route, so multi-file pages render."""
    await _require_project(pid)
    if root not in ("repo", "docs"):
        raise HTTPException(status_code=400, detail="root must be 'repo' or 'docs'")
    target = _resolve(pid, f"{root}/{path}")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"no such file: {path}")
    return FileResponse(target)


@router.put("/projects/{pid}/artifacts/content")
async def write_artifact_file(pid: str, req: FileWriteRequest, root: str = Query("repo")) -> dict:
    """Human file editing from the UI — repo files only (docs are the
    pipeline's own record and shouldn't be edited out from under it)."""
    await _require_project(pid)
    if root != "repo":
        raise HTTPException(status_code=400, detail="only repo files are editable")
    target = _resolve(pid, f"repo/{req.path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    return {"saved": req.path, "bytes": len(req.content.encode('utf-8'))}
