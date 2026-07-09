"""Auto-generates a short project title and one-line summary.

`project_store.create_project` seeds a fallback title (the idea, trimmed) so
the sidebar never shows nothing. Once the scope document exists, this module
asks the fast/cheap model for a punchier title and a plain-language summary,
then republishes them — the sidebar and pipeline header update live via the
`project_updated` event, no page reload needed.

This deliberately runs off the critical path (fire-and-forget from the graph
node that drafts the scope doc): a slow or failed title-generation call should
never hold up the actual pipeline.
"""
from __future__ import annotations

import json
import logging

from app.config import get_settings
from app.llm.client import chat_completion
from app.orchestration.events import publish_event
from app.services import project_store as store

log = logging.getLogger("asterion.summarizer")

SYSTEM_PROMPT = """You name software projects for a non-technical audience.
Given a project idea (and sometimes a scope document), respond with strict JSON:
{"title": "...", "summary": "..."}

Rules for "title":
- 3 to 6 words, title case, no trailing punctuation.
- Describe what the product IS, not the raw request (e.g. "Reading List Tracker", not "Build me a tool that...").

Rules for "summary":
- One sentence (under 140 characters), plain language, no jargon.
- Describe what it does for the user, not how it's implemented.

Respond with ONLY the JSON object, nothing else."""


def _fallback(idea: str) -> tuple[str, str]:
    title = idea.strip().splitlines()[0][:60] or "Untitled project"
    return title, ""


async def refresh_title_summary(pid: str, idea: str, scope_doc: str = "") -> None:
    """Generate and persist a friendlier title/summary. Never raises."""
    try:
        user_content = f"Project idea:\n{idea}"
        if scope_doc:
            user_content += f"\n\nScope document:\n{scope_doc[:2000]}"

        response = await chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            model=get_settings().groq_fast_model,
            temperature=0.3,
            max_tokens=150,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        title = str(parsed.get("title") or "").strip()[:80]
        summary = str(parsed.get("summary") or "").strip()[:200]
        if not title:
            title, _ = _fallback(idea)
    except Exception:  # noqa: BLE001 - best-effort; the raw idea is a fine fallback
        log.warning("Title/summary generation failed for %s", pid, exc_info=True)
        title, summary = _fallback(idea)
        if not scope_doc:
            return  # keep the create_project fallback rather than overwriting with the same thing

    await store.set_title_summary(pid, title, summary)
    await publish_event(pid, "project_updated", "system", "Updated project title", {"title": title, "summary": summary})
