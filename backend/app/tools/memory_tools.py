"""Cross-agent memory — store/recall notes scoped to a project.

This is a hashed-bag-of-words + NumPy-cosine placeholder, not real embeddings
(Groq has no embeddings endpoint; Phase 7 swaps in fastembed/ONNX vectors
computed locally). The storage shape and tool interface are final — Phase 7
only changes how `_vectorize` turns text into a vector, so nothing above this
module needs to change later.
"""
from __future__ import annotations

import json
import re
import time

import numpy as np

from app.redis.client import get_redis, key
from app.tools.registry import ToolContext, register

DIM = 256
_WORD_RE = re.compile(r"[a-z0-9]+")


def _vectorize(text: str) -> np.ndarray:
    vec = np.zeros(DIM, dtype=np.float32)
    for word in _WORD_RE.findall(text.lower()):
        vec[hash(word) % DIM] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def _mem_key(project_id: str) -> str:
    return key("memory", project_id)


@register(
    name="remember",
    description="Save a short note to shared project memory so later agents/tickets can recall it.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The note to remember."},
            "tag": {"type": "string", "description": "Optional short label, e.g. 'decision', 'constraint'."},
        },
        "required": ["text"],
    },
    agents=["scope", "architect", "planner", "developer", "reviewer", "debugger"],
)
async def remember(ctx: ToolContext, text: str, tag: str = "") -> dict:
    r = await get_redis()
    entry = {"text": text, "tag": tag, "agent": ctx.agent, "ts": time.time()}
    await r.rpush(_mem_key(ctx.project_id), json.dumps(entry))
    return {"ok": True}


@register(
    name="recall",
    description="Search shared project memory for notes relevant to a query.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "top_k": {"type": "integer", "description": "Max notes to return.", "default": 5},
        },
        "required": ["query"],
    },
    agents=["scope", "architect", "planner", "developer", "reviewer", "debugger"],
)
async def recall(ctx: ToolContext, query: str, top_k: int = 5) -> dict:
    r = await get_redis()
    raw = await r.lrange(_mem_key(ctx.project_id), 0, -1)
    if not raw:
        return {"notes": []}

    entries = [json.loads(b) for b in raw]
    query_vec = _vectorize(query)
    scored = []
    for entry in entries:
        sim = float(np.dot(query_vec, _vectorize(entry["text"])))
        scored.append((sim, entry))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = [entry for sim, entry in scored[:top_k] if sim > 0]
    return {"notes": top}
