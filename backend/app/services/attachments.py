"""Reads uploaded files (PDF, image, text) into plain-text context the agents
can consume.

An upload becomes a staged *batch* under `uploads_dir/<batch_id>/`. Each file's
text is extracted once, at upload time, and cached as a `<name>.extracted.txt`
sidecar so a run can re-load it cheaply. PDFs use pypdf; images are described by
a Groq vision model (OCR-style); text-like files are decoded directly.
Everything is capped so a large document can't blow the tight free-tier token
budget when it's injected into a run.
"""
from __future__ import annotations

import base64
import logging
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import get_settings
from app.llm.client import chat_completion

log = logging.getLogger("asterion.attachments")

# llama-4-scout accepts image content parts and has the highest TPM headroom.
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

_PER_FILE_CHARS = 6000   # cap on any one file's extracted text
_BATCH_CHARS = 8000      # cap on the combined blob injected into a run
_PDF_MAX_PAGES = 40

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".yaml", ".yml", ".xml", ".html", ".htm"}
_PDF_EXT = {".pdf"}
ALLOWED_EXT = _IMAGE_EXT | _TEXT_EXT | _PDF_EXT

_EXTRACTED_SUFFIX = ".extracted.txt"
_IMAGE_PROMPT = (
    "Describe this image in thorough detail and transcribe any visible text verbatim. "
    "Be specific about content, structure/layout, data, diagrams, and anything a "
    "developer or researcher would need to act on it."
)


@dataclass
class StagedFile:
    name: str
    kind: str
    chars: int


def kind_for(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in _PDF_EXT:
        return "pdf"
    if ext in _IMAGE_EXT:
        return "image"
    if ext in _TEXT_EXT:
        return "text"
    return "other"


def _safe_name(name: str) -> str:
    """basename only — never let an upload's name escape the batch dir."""
    base = Path(name).name.strip() or "file"
    return base.replace("\\", "_").replace("/", "_")


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001 — a broken PDF shouldn't fail the upload
        return f"[could not read PDF: {exc}]"
    parts: list[str] = []
    for page in reader.pages[:_PDF_MAX_PAGES]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 — skip an unreadable page, keep the rest
            continue
    text = "\n".join(parts).strip()
    return text or "[PDF had no extractable text — it may be scanned images]"


def _extract_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        return f"[could not read file: {exc}]"


async def _extract_image(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    try:
        resp = await chat_completion(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": _IMAGE_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            model=VISION_MODEL,
            temperature=0.2,
            max_tokens=1024,
        )
        return (resp.choices[0].message.content or "").strip() or "[no description produced]"
    except Exception as exc:  # noqa: BLE001 — degrade to a note; never fail the upload
        log.warning("image analysis failed for %s: %s", path.name, exc)
        return f"[could not analyze image: {exc}]"


async def _extract(path: Path) -> str:
    kind = kind_for(path.name)
    if kind == "pdf":
        text = _extract_pdf(path)
    elif kind == "image":
        text = await _extract_image(path)
    elif kind == "text":
        text = _extract_text(path)
    else:
        text = "[unsupported file type]"
    return text[:_PER_FILE_CHARS]


async def stage_batch(files: list[tuple[str, bytes]]) -> tuple[str, list[StagedFile]]:
    """Persist and extract a set of (filename, bytes). Returns (batch_id, metas)."""
    batch_id = uuid.uuid4().hex[:12]
    root = get_settings().uploads_dir / batch_id
    root.mkdir(parents=True, exist_ok=True)
    metas: list[StagedFile] = []
    for name, data in files:
        safe = _safe_name(name)
        (root / safe).write_bytes(data)
        text = await _extract(root / safe)
        (root / f"{safe}{_EXTRACTED_SUFFIX}").write_text(text, encoding="utf-8")
        metas.append(StagedFile(name=safe, kind=kind_for(safe), chars=len(text)))
    return batch_id, metas


def _batch_dir(batch_id: str) -> Path:
    # basename guard: a batch_id is our own hex, never a path
    return get_settings().uploads_dir / Path(batch_id).name


def context_for(batch_id: str) -> str:
    """Combined, capped extracted text of a staged batch, for prompt injection."""
    root = _batch_dir(batch_id)
    if not batch_id or not root.exists():
        return ""
    blocks: list[str] = []
    for sidecar in sorted(root.glob(f"*{_EXTRACTED_SUFFIX}")):
        name = sidecar.name[: -len(_EXTRACTED_SUFFIX)]
        body = sidecar.read_text(encoding="utf-8").strip()
        if body:
            blocks.append(f"### {name}\n{body}")
    return "\n\n".join(blocks).strip()[:_BATCH_CHARS]


def consume(batch_id: str, docs_dir: Path | None = None) -> str:
    """Return the batch's combined context, optionally record it in the project's
    docs, then delete the staging dir. Safe to call with an empty/unknown id."""
    context = context_for(batch_id)
    if context and docs_dir is not None:
        try:
            docs_dir.mkdir(parents=True, exist_ok=True)
            (docs_dir / "attachments.md").write_text(
                f"# Attached documents\n\n{context}\n", encoding="utf-8"
            )
        except OSError as exc:
            log.warning("couldn't persist attachments.md: %s", exc)
    root = _batch_dir(batch_id)
    if batch_id and root.exists():
        shutil.rmtree(root, ignore_errors=True)
    return context


def as_meta_dicts(metas: list[StagedFile]) -> list[dict]:
    return [asdict(m) for m in metas]


def augment_query(query: str, context: str) -> str:
    """Prepend extracted attachment context so the run is grounded in the files."""
    if not context:
        return query
    return (
        "The user attached documents — use their content as primary context for this request.\n\n"
        f"{context}\n\n---\n\n{query}"
    )
