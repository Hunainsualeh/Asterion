"""Intent-specific DAG builders and node executors.

`build_dag_for(intent, query)` shapes the run:
  trivial  → one fast-path node (weather lookup, direct answer)
  simple   → one specialist node (code task, web search, draft)
  moderate → the canonical fan-out:  research ‖ analyze  →  summarize
  complex  → planner root that *dynamically expands* the DAG with worker
             nodes fanned out per subtask, joined by a final summarizer

Executors receive an `ExecutionContext` (query, params, upstream outputs) and
return markdown/plain text — or an `Expansion` to grow the DAG mid-run.
Every LLM call goes through the same per-agent routing chains as the SDLC
agents (litellm_config.yaml) and reports token/latency metrics.
"""
from __future__ import annotations

import json
import logging
import re
import time

from app.dag.engine import ExecutionContext, Expansion, NodeSpec, RetryPolicy
from app.llm import guidelines
from app.llm.client import chat_completion
from app.llm.routing import chain_for
from app.observability import record_llm_call
from app.orchestration.intent import Intent
from app.tools.registry import ToolContext
from app.tools.weather import WeatherError, get_weather

log = logging.getLogger("asterion.dag.workflows")

MAX_UPSTREAM_CHARS = 6000  # per-dependency context budget fed into fan-in nodes


# ---------------------------------------------------------------------------
# Shared LLM helper: walk the agent's routing chain, record metrics
# ---------------------------------------------------------------------------
async def _complete_resp(
    project_id: str,
    agent: str,
    messages: list[dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    response_format: dict | None = None,
    timeout_s: float | None = None,
):
    last_exc: Exception | None = None
    for model in chain_for(agent):
        start = time.monotonic()
        try:
            kwargs: dict = {}
            if timeout_s is not None:
                kwargs["timeout_s"] = timeout_s
            resp = await chat_completion(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                **kwargs,
            )
            usage = getattr(resp, "usage", None)
            await record_llm_call(
                project_id,
                agent,
                getattr(resp, "model", model),
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                latency_ms=int((time.monotonic() - start) * 1000),
            )
            return resp
        except Exception as exc:  # noqa: BLE001 — try the next model in the chain
            last_exc = exc
            await record_llm_call(
                project_id, agent, model,
                latency_ms=int((time.monotonic() - start) * 1000),
                ok=False, error=str(exc),
            )
            log.warning("%s: model %s failed (%s), trying next in chain", agent, model, exc)
    raise last_exc if last_exc else RuntimeError(f"no models configured for {agent}")


async def _complete(
    project_id: str,
    agent: str,
    messages: list[dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    response_format: dict | None = None,
) -> str:
    resp = await _complete_resp(
        project_id, agent, messages,
        temperature=temperature, max_tokens=max_tokens, response_format=response_format,
    )
    return resp.choices[0].message.content or ""


async def _complete_long(
    project_id: str,
    agent: str,
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 8000,
    rounds: int = 3,
    timeout_s: float = 90.0,
) -> str:
    """Completion that survives the max_tokens ceiling: when the model stops
    with finish_reason=length, ask it to continue exactly where it left off
    and stitch the pieces — long code output never breaks in half again."""
    convo = list(messages)
    parts: list[str] = []
    for _ in range(rounds):
        resp = await _complete_resp(
            project_id, agent, convo,
            temperature=temperature, max_tokens=max_tokens, timeout_s=timeout_s,
        )
        choice = resp.choices[0]
        text = choice.message.content or ""
        parts.append(text)
        if getattr(choice, "finish_reason", None) != "length":
            break
        convo = convo + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": (
                "You hit the output limit mid-stream. Continue EXACTLY from the last "
                "character you produced — no repetition, no restarting the file, no preamble."
            )},
        ]
    return "".join(parts)


def _history_block(ctx: ExecutionContext) -> str:
    """Prior conversation turns (set by the task runner on follow-up runs),
    formatted for prompt injection. Empty string on a project's first run."""
    history = str(ctx.meta.get("history") or "").strip()
    return f"Conversation so far:\n{history}\n\n" if history else ""


def _upstream_context(ctx: ExecutionContext) -> str:
    parts = []
    for dep_id, output in ctx.dep_outputs().items():
        if output is None:
            continue
        text = output if isinstance(output, str) else json.dumps(output, default=str)
        parts.append(f"### Output of step '{dep_id}':\n{text[:MAX_UPSTREAM_CHARS]}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------
async def exec_answer(ctx: ExecutionContext) -> str:
    """Direct single-shot answer — the fast path for chat/qa/writing."""
    style = ctx.node.params.get("style", "")
    system = (
        "You are Asterion, a helpful, direct assistant. Answer the user's request "
        "completely and concretely in markdown. If you genuinely lack the live data "
        "needed, say what you'd need — never answer with filler."
    )
    if style == "writing":
        system = (
            "You are a skilled professional writer. Produce the requested text in "
            "polished final form, ready to use. Markdown formatting."
        )
    upstream = _upstream_context(ctx)
    user = ctx.node.params.get("query", ctx.query)
    if upstream:
        user = f"{user}\n\nUse this gathered material:\n\n{upstream}"
    history = _history_block(ctx)
    if history:
        user = f"{history}User's new message: {user}"
    return await _complete(ctx.project_id, "answer", [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])


async def exec_weather(ctx: ExecutionContext) -> str:
    location = ctx.node.params.get("location") or ""
    if not location:
        # last-resort extraction so the fast path still works without a slot
        m = re.search(r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z .'-]{1,40}?)(?:[?.!,]|$)", ctx.query)
        location = m.group(1).strip() if m else ""
    if not location:
        return await exec_research(ctx)  # let web search figure it out
    try:
        report = await get_weather(location)
        return report["answer"]
    except WeatherError as exc:
        return f"I couldn't look that up directly ({exc}). " + await exec_research(ctx)


async def exec_research(ctx: ExecutionContext) -> str:
    """Live web research via Groq's compound model (server-side web search)."""
    query = ctx.node.params.get("query", ctx.query)
    history = _history_block(ctx)
    if history:
        query = f"{history}New request: {query}"
    start = time.monotonic()
    try:
        resp = await chat_completion(
            messages=[{
                "role": "user",
                "content": f"Research this and answer with concrete, current facts (cite sources inline): {query}",
            }],
            model="groq/compound",
            temperature=0.2,
            max_tokens=2048,
        )
        usage = getattr(resp, "usage", None)
        await record_llm_call(
            ctx.project_id, "research", "groq/compound",
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=int((time.monotonic() - start) * 1000),
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001 — degrade to model knowledge instead of failing the branch
        await record_llm_call(ctx.project_id, "research", "groq/compound",
                              latency_ms=int((time.monotonic() - start) * 1000), ok=False, error=str(exc))
        log.warning("compound research failed (%s); falling back to model knowledge", exc)
        return await _complete(ctx.project_id, "research", [
            {"role": "system", "content": (
                "Live web search is unavailable. Answer from your knowledge, be explicit "
                "about anything that may be out of date, and clearly mark uncertainty."
            )},
            {"role": "user", "content": query},
        ])


async def exec_analyze(ctx: ExecutionContext) -> str:
    query = ctx.node.params.get("query", ctx.query)
    upstream = _upstream_context(ctx)
    user = f"{_history_block(ctx)}Task: {query}"
    if upstream:
        user += f"\n\nMaterial gathered so far:\n\n{upstream}"
    return await _complete(ctx.project_id, "analyze", [
        {"role": "system", "content": (
            "You are an analyst. Break the subject down: key factors, trade-offs, "
            "comparisons, risks, and a clear bottom line. Structured markdown."
        )},
        {"role": "user", "content": user},
    ])


async def exec_summarize(ctx: ExecutionContext) -> str:
    upstream = _upstream_context(ctx)
    missing = [d for d in ctx.node.deps if d not in ctx.outputs]
    note = f"\n\n(Note: steps {', '.join(missing)} failed — synthesize from what's available.)" if missing else ""
    return await _complete(ctx.project_id, "summarize", [
        {"role": "system", "content": (
            "You synthesize multiple working notes into ONE final deliverable for the "
            "user. Lead with the direct answer/result, then supporting detail. "
            "Never mention 'steps', 'agents' or internal process. Markdown."
        )},
        {"role": "user", "content": f"{_history_block(ctx)}User's request: {ctx.query}\n\n{upstream}{note}"},
    ], max_tokens=3000)


async def exec_planner(ctx: ExecutionContext) -> Expansion:
    """Root node for complex tasks: produce a plan and dynamically expand the
    DAG with parallel worker nodes plus a final synthesizer."""
    raw = await _complete(ctx.project_id, "planner", [
        {"role": "system", "content": (
            "You decompose a complex request into 2-5 INDEPENDENT subtasks that can "
            "run in parallel, each a research or analysis unit with a self-contained "
            "instruction. Return ONLY JSON:\n"
            '{"subtasks": [{"id": "s1", "title": "...", "kind": "research|analyze", '
            '"query": "full self-contained instruction"}]}'
        )},
        {"role": "user", "content": ctx.query},
    ], temperature=0.1, max_tokens=1200, response_format={"type": "json_object"})

    try:
        plan = json.loads(raw)
        subtasks = plan.get("subtasks") or []
    except json.JSONDecodeError:
        subtasks = []
    if not subtasks:
        # degenerate plan → canonical research+analyze shape
        subtasks = [
            {"id": "s1", "title": "Research", "kind": "research", "query": ctx.query},
            {"id": "s2", "title": "Analyze", "kind": "analyze", "query": ctx.query},
        ]

    worker_ids: list[str] = []
    nodes: list[NodeSpec] = []
    for i, sub in enumerate(subtasks[:5], 1):
        sid = re.sub(r"[^a-zA-Z0-9_-]", "", str(sub.get("id") or f"s{i}")) or f"s{i}"
        if sid in worker_ids:
            sid = f"{sid}_{i}"
        kind = sub.get("kind") if sub.get("kind") in ("research", "analyze") else "research"
        nodes.append(NodeSpec(
            id=sid,
            agent=kind,
            name=str(sub.get("title") or f"Subtask {i}")[:60],
            params={"query": str(sub.get("query") or ctx.query)},
            deps=[ctx.node.id],
            timeout_s=150,
            retry=RetryPolicy(max_attempts=2),
        ))
        worker_ids.append(sid)

    nodes.append(NodeSpec(
        id="summarize",
        agent="summarize",
        name="Synthesize final answer",
        deps=worker_ids,
        timeout_s=150,
        retry=RetryPolicy(max_attempts=2),
        allow_failed_deps=True,
        is_final=True,
    ))
    plan_md = "\n".join(f"- **{n.name}** (`{n.agent}`)" for n in nodes[:-1])
    return Expansion(nodes=nodes, result=f"Execution plan:\n{plan_md}")


async def exec_deep_research_plan(ctx: ExecutionContext) -> Expansion:
    """Root node for Deep Research: split the topic into focused sub-questions,
    fan them out to the web-research executor, and join into one comprehensive
    report. Attached-document context (when present) is already in ctx.query."""
    raw = await _complete(ctx.project_id, "planner", [
        {"role": "system", "content": (
            "You are a lead researcher planning a DEEP investigation. Break the user's topic "
            "into 4-6 focused, non-overlapping sub-questions that together cover it "
            "comprehensively — background, key facets, comparisons, current state, risks, and "
            "practical implications. Return ONLY JSON:\n"
            '{"subtasks": [{"id": "s1", "title": "...", '
            '"query": "a full, self-contained research question"}]}'
        )},
        {"role": "user", "content": ctx.query},
    ], temperature=0.2, max_tokens=1200, response_format={"type": "json_object"})

    try:
        subtasks = json.loads(raw).get("subtasks") or []
    except json.JSONDecodeError:
        subtasks = []
    if not subtasks:
        subtasks = [{"id": "s1", "title": "Investigation", "query": ctx.query}]

    worker_ids: list[str] = []
    nodes: list[NodeSpec] = []
    for i, sub in enumerate(subtasks[:6], 1):
        sid = re.sub(r"[^a-zA-Z0-9_-]", "", str(sub.get("id") or f"s{i}")) or f"s{i}"
        if sid in worker_ids:
            sid = f"{sid}_{i}"
        nodes.append(NodeSpec(
            id=sid,
            agent="research",
            name=str(sub.get("title") or f"Sub-question {i}")[:60],
            params={"query": str(sub.get("query") or ctx.query)},
            deps=[ctx.node.id],
            timeout_s=150,
            retry=RetryPolicy(max_attempts=2),
        ))
        worker_ids.append(sid)

    nodes.append(NodeSpec(
        id="report",
        agent="deep_synthesize",
        name="Compile research report",
        deps=worker_ids,
        timeout_s=200,
        retry=RetryPolicy(max_attempts=2),
        allow_failed_deps=True,
        is_final=True,
    ))
    plan_md = "\n".join(f"- {n.name}" for n in nodes[:-1])
    return Expansion(nodes=nodes, result=f"Research plan:\n{plan_md}")


async def exec_deep_synthesize(ctx: ExecutionContext) -> str:
    """Compile the sub-question findings into one comprehensive markdown report."""
    upstream = _upstream_context(ctx)
    missing = [d for d in ctx.node.deps if d not in ctx.outputs]
    note = f"\n\n(Some lines of inquiry returned nothing: {', '.join(missing)}.)" if missing else ""
    return await _complete_long(ctx.project_id, "summarize", [
        {"role": "system", "content": (
            "You are compiling a COMPREHENSIVE research report from the findings of several "
            "sub-investigations. Write for the user directly — never mention 'steps', 'nodes', "
            "'agents', or internal process. Markdown, in this structure:\n"
            "1. **Executive summary** — the direct answer up top, 3-5 sentences.\n"
            "2. **Detailed findings** — one `##` section per major theme, with specifics, "
            "figures, and comparisons; synthesize across sources rather than listing them.\n"
            "3. **Key takeaways** — a tight bulleted list.\n"
            "4. **Sources** — the notable URLs/references gathered.\n"
            "Be thorough, specific, and well-organized; prefer concrete detail over generality."
        )},
        {"role": "user", "content": f"{_history_block(ctx)}Research topic: {ctx.query}\n\nFindings:\n\n{upstream}{note}"},
    ], temperature=0.3, max_tokens=6000, rounds=2, timeout_s=120.0)


_CODER_SYSTEM = """You are the Developer — a staff-level software engineer. You deliver COMPLETE, working, production-quality code that makes another engineer stop and say "this is genuinely well built." Aim for excellence, not adequacy: build the careful, thorough, obvious-in-hindsight solution — never the shallowest thing that merely runs.

Hard rules:
- Every file is COMPLETE from first line to last. Never placeholders, ellipses, TODOs, fake/stub data pretending to be real, or "rest of the code stays the same".
- The code runs as-is: all imports, entry points, and wiring included.
- Engineer it properly — do NOT ship the shallowest thing that "works":
  - Structure the code: separate concerns, factor reusable pieces into well-named functions/modules/components, and split files along real boundaries instead of one giant file mixing everything.
  - Handle reality: validate inputs, cover edge cases, handle and surface errors, and implement empty/loading/error states for any UI. Every interaction must actually work, not just look clickable.
  - Make it excellent: thoughtful naming, clear comments where intent isn't obvious, sensible defaults, and the small details that show craft. For any UI, make it genuinely polished and modern — considered layout, spacing, typography, colour, states, and micro-interactions — not a default-styled skeleton.
  - Match the request's ambition. If the user asks for a real app (a clone, a dashboard, a tool), build something with genuine depth and real features — not a toy.
- Choose the stack that best fits the request and its scale: a single self-contained file only when that genuinely suits a tiny UI; otherwise a sensible multi-file structure. State the choice in one line and why.

Output format — follow EXACTLY, no other headings or commentary between files:
One short paragraph: what you built, key decisions, how to run it. Then each file as:

### FILE: relative/path.ext
```lang
<complete file content>
```
"""

# `### FILE: <path>` headers followed by one fenced block each; the fence
# closes at the first ``` on its own line.
_FILE_BLOCK_RE = re.compile(
    r"^### FILE:[ \t]*(?P<path>[^\n]+?)[ \t]*\n```[\w+.-]*[ \t]*\n(?P<body>.*?)\n```",
    re.DOTALL | re.MULTILINE,
)

# Bounds for feeding the existing workspace back into an edit request —
# enough for a real small app, without blowing the model's TPM ceiling.
_EDIT_MAX_FILES = 10
_EDIT_MAX_FILE_CHARS = 8_000
_EDIT_MAX_TOTAL_CHARS = 20_000
_EDIT_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".next"}


def _existing_repo_files(project_id: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(files included as (path, content), paths listed but omitted for size)."""
    from app.config import get_settings

    root = get_settings().workspace_dir / project_id / "repo"
    if not root.exists():
        return [], []
    included: list[tuple[str, str]] = []
    omitted: list[str] = []
    total = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in _EDIT_SKIP_DIRS for part in rel.parts):
            continue
        rel_s = str(rel).replace("\\", "/")
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if (len(included) >= _EDIT_MAX_FILES or len(text) > _EDIT_MAX_FILE_CHARS
                or total + len(text) > _EDIT_MAX_TOTAL_CHARS):
            omitted.append(rel_s)
            continue
        included.append((rel_s, text))
        total += len(text)
    return included, omitted


async def _save_file_blocks(ctx: ExecutionContext, markdown: str, agent: str) -> list[str]:
    """Persist every `### FILE:` block in `markdown` to the project workspace."""
    from app.tools.fs_tools import write_file

    tool_ctx = ToolContext(project_id=ctx.project_id, agent=agent)
    saved: list[str] = []
    for m in list(_FILE_BLOCK_RE.finditer(markdown))[:30]:
        path, body = m.group("path").strip().strip("`"), m.group("body")
        if not path or not body.strip():
            continue
        try:
            await write_file(tool_ctx, path=path, content=body)
            saved.append(path)
        except Exception as exc:  # noqa: BLE001 — a bad path shouldn't kill the deliverable
            log.warning("couldn't save %s: %s", path, exc)
    return saved


async def exec_code(ctx: ExecutionContext) -> str:
    """Developer step: produce complete files as markdown (rendered directly
    in chat with syntax highlighting), persist them to the workspace. Output
    is plain markdown, not JSON — JSON string-escaping of whole source files
    is what used to truncate and mangle long code.

    When the workspace already has code, this becomes an EDIT: the existing
    files are shown to the model and it may only re-emit the ones it changed —
    a "make the button blue" follow-up no longer rebuilds the whole app."""
    query = ctx.node.params.get("query", ctx.query)
    upstream = _upstream_context(ctx)  # the implementation plan, when one exists
    user = f"{_history_block(ctx)}{query}"
    if upstream:
        user += f"\n\nFollow this implementation plan (deviate only with a stated reason):\n\n{upstream}"

    system = _CODER_SYSTEM + "\n\n" + guidelines.for_agent("coder")
    existing, omitted = _existing_repo_files(ctx.project_id)
    if existing:
        current = "\n\n".join(
            f"### FILE: {path}\n```\n{content}\n```" for path, content in existing
        )
        if omitted:
            current += "\n\n(Also present, not shown: " + ", ".join(omitted) + ")"
        user += (
            "\n\nThe project workspace ALREADY CONTAINS this code:\n\n" + current +
            "\n\nThis is a change to an existing project. Modify ONLY what the request "
            "requires. Output ONLY the files you changed or added — each one complete — "
            "and do NOT re-emit unchanged files. Do not rebuild from scratch. Start with "
            "one line saying exactly what you changed and where."
        )

    raw = await _complete_long(ctx.project_id, "coder", [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0.15, max_tokens=8000, timeout_s=90.0)

    if not raw.strip():
        return await exec_answer(ctx)
    saved = await _save_file_blocks(ctx, raw, "developer")
    if saved:
        raw += f"\n\n---\n_Saved to the project workspace: {', '.join(f'`{p}`' for p in saved)}_"
    elif "### FILE:" not in raw and "```" not in raw:
        # Conversational ask misrouted as coding — the direct answer is fine.
        return raw
    return raw


async def exec_design(ctx: ExecutionContext) -> str:
    """UI/UX Designer step: a concrete design spec (layout, palette, states,
    interactions) the engineer implements — so UIs stop looking default."""
    query = ctx.node.params.get("query", ctx.query)
    upstream = _upstream_context(ctx)
    user = f"{_history_block(ctx)}{query}"
    if upstream:
        user += f"\n\nBuild plan so far:\n\n{upstream}"
    return await _complete(ctx.project_id, "designer", [
        {"role": "system", "content": (
            "You are a senior UI/UX designer. Produce a concrete, implementable design "
            "spec for this product — not vague principles. Markdown, in this order:\n"
            "1. **Layout** — each screen/page, its sections, and hierarchy.\n"
            "2. **Visual style** — exact palette (hex values), font stack, spacing scale, "
            "corner radii, shadows. Pick a coherent, modern direction; no defaults.\n"
            "3. **Components & states** — buttons, inputs, cards, empty/loading/error states.\n"
            "4. **Interactions** — hover/focus/transition behavior worth specifying.\n"
            "5. **Responsive & accessibility** — breakpoints, contrast, keyboard use.\n"
            "Be decisive and brief; the engineer follows this verbatim.\n\n"
            + guidelines.for_agent("designer")
        )},
        {"role": "user", "content": user},
    ], temperature=0.4, max_tokens=1800)


async def exec_code_plan(ctx: ExecutionContext) -> str:
    """Tech-lead step for moderate builds: turn the request into a concrete,
    file-by-file implementation plan the developer follows."""
    query = ctx.node.params.get("query", ctx.query)
    system = (
        "You are the Tech Lead writing the technical specification for a build the "
        "Developer will follow verbatim. Be concrete and decisive; no filler, no "
        "alternatives-essay. Markdown, in this order:\n"
        "1. **Goal & stack** — restate the goal; choose the stack that best fits the "
        "request and its scale (not merely the simplest), with sensible defaults for "
        "anything unspecified.\n"
        "2. **Architecture** — the components/modules and their responsibilities, how "
        "they connect, and the data/state model. Design for separation of concerns.\n"
        "3. **Files** — every file to create, its purpose, and its key functions/components.\n"
        "4. **Error handling & edge cases** — what can go wrong and how the build must "
        "handle it (invalid input, empty/loading/error states, failures).\n"
        "5. **Acceptance criteria** — the specific, demanding checks that must pass for "
        "this to be done.\n\n"
        + guidelines.for_agent("planner")
    )
    return await _complete(ctx.project_id, "planner", [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{_history_block(ctx)}{query}"},
    ], temperature=0.2, max_tokens=1800)


async def exec_code_review(ctx: ExecutionContext) -> str:
    """Reviewer/tester step: verify the developer's output is complete and
    correct; ship corrected complete files when something is actually broken."""
    code_md = ""
    for dep in ctx.node.deps:
        out = ctx.outputs.get(dep)
        if isinstance(out, str) and "### FILE:" in out:
            code_md = out
            break
    if not code_md:
        # nothing reviewable — pass through whatever the developer produced
        return str(ctx.outputs.get(ctx.node.deps[0], "")) if ctx.node.deps else ""

    review = await _complete_long(ctx.project_id, "reviewer", [
        {"role": "system", "content": (
            "You are the QA Engineer: review AND verify the delivered code against the user's "
            "request. Judge it, in order: (1) completeness — no truncated files, no placeholders, "
            "nothing referenced but missing; (2) correctness — logic, edge cases, obvious runtime "
            "errors; (3) it runs as-is — imports, wiring, entry point; (4) depth & quality — real "
            "structure and separation of concerns, error/empty states handled, accessibility for "
            "any UI, interactions that actually work. Shallow or skeleton work that merely runs is "
            "NOT a pass.\n"
            "If you find a REAL problem, output the corrected COMPLETE file using the exact format "
            "`### FILE: path` followed by one fenced code block (only files you fixed), then a short "
            "bullet list of what was wrong and its severity.\n"
            "If it genuinely holds up, reply `LGTM` plus 2-3 bullets on what you verified.\n"
            "Never rewrite working code for pure taste.\n\n"
            + guidelines.for_agent("reviewer")
        )},
        {"role": "user", "content": (
            f"User's request: {ctx.query}\n\nDelivered code:\n\n{code_md[:24000]}"
        )},
    ], temperature=0.1, max_tokens=8000, timeout_s=90.0)

    fixed = await _save_file_blocks(ctx, review, "reviewer")
    section = "## Review\n\n" + (review.strip() or "LGTM")
    if fixed:
        section += f"\n\n_Corrected files saved: {', '.join(f'`{p}`' for p in fixed)}_"
    return f"{code_md}\n\n---\n\n{section}"


EXECUTORS = {
    "answer": exec_answer,
    "weather": exec_weather,
    "research": exec_research,
    "analyze": exec_analyze,
    "summarize": exec_summarize,
    "planner": exec_planner,
    "deep_research_plan": exec_deep_research_plan,
    "deep_synthesize": exec_deep_synthesize,
    "code": exec_code,
    "code_plan": exec_code_plan,
    "code_review": exec_code_review,
    "designer": exec_design,
}

_NO_UI_RE = re.compile(
    r"\b(cli|command.line|terminal|script only|api only|backend only|no ui|headless|library|package)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# DAG shapes per intent/complexity
# ---------------------------------------------------------------------------
def build_deep_research_nodes(query: str) -> list[NodeSpec]:
    """Deep Research mode: a planner root that expands into parallel web-research
    sub-questions joined by a comprehensive report synthesizer."""
    return [NodeSpec(id="research_plan", agent="deep_research_plan", name="Plan the research",
                     timeout_s=120, retry=RetryPolicy(max_attempts=2))]


def build_nodes_for(intent: Intent, query: str) -> list[NodeSpec]:
    kind, cx = intent.kind, intent.complexity

    if kind == "weather":
        return [NodeSpec(id="weather", agent="weather", name="Weather lookup",
                         params={"location": intent.slots.get("location", "")},
                         timeout_s=45, retry=RetryPolicy(max_attempts=3, backoff_base_s=1.0), is_final=True)]

    if kind == "coding":
        # Anything beyond a throwaway one-liner gets the full dev team so real
        # build requests actually spawn agents (Tech Lead → UI/UX Designer ‖,
        # Software Engineer, QA Engineer). Only "trivial" (e.g. "print hello
        # world") collapses to a single build node.
        if cx != "trivial":
            # The lightweight dev team: Tech Lead plans, UI/UX Designer specs
            # the interface (plan ‖ design run in parallel), Software Engineer
            # implements both, QA Engineer verifies and repairs.
            has_ui = not _NO_UI_RE.search(query)
            nodes = [
                NodeSpec(id="plan", agent="code_plan", name="Tech Lead · plan",
                         timeout_s=120, retry=RetryPolicy(max_attempts=2)),
            ]
            dev_deps = ["plan"]
            if has_ui:
                nodes.append(NodeSpec(id="design", agent="designer", name="UI/UX Designer · design spec",
                                      timeout_s=120, retry=RetryPolicy(max_attempts=2)))
                dev_deps.append("design")
            nodes.append(NodeSpec(id="code", agent="code", name="Software Engineer · build",
                                  deps=dev_deps, timeout_s=420, retry=RetryPolicy(max_attempts=2),
                                  allow_failed_deps=True))
            nodes.append(NodeSpec(id="review", agent="code_review", name="QA Engineer · review & verify",
                                  deps=["code"], timeout_s=300, retry=RetryPolicy(max_attempts=1),
                                  is_final=True))
            return nodes
        return [NodeSpec(id="code", agent="code", name="Software Engineer · build",
                         timeout_s=360, retry=RetryPolicy(max_attempts=2), is_final=True)]

    if kind == "search":
        return [NodeSpec(id="search", agent="research", name="Web search",
                         timeout_s=120, retry=RetryPolicy(max_attempts=2), is_final=True)]

    if kind in ("chat", "writing", "qa") and cx in ("trivial", "simple"):
        style = "writing" if kind == "writing" else ""
        return [NodeSpec(id="answer", agent="answer", name="Answer",
                         params={"style": style}, timeout_s=120,
                         retry=RetryPolicy(max_attempts=2), is_final=True)]

    if cx == "complex":
        return [NodeSpec(id="planner", agent="planner", name="Plan the work",
                         timeout_s=120, retry=RetryPolicy(max_attempts=2))]

    # moderate default — the canonical parallel fan-out/fan-in
    return [
        NodeSpec(id="research", agent="research", name="Research",
                 timeout_s=150, retry=RetryPolicy(max_attempts=2)),
        NodeSpec(id="analyze", agent="analyze", name="Analyze",
                 timeout_s=150, retry=RetryPolicy(max_attempts=2)),
        NodeSpec(id="summarize", agent="summarize", name="Synthesize final answer",
                 deps=["research", "analyze"], timeout_s=150,
                 retry=RetryPolicy(max_attempts=2), allow_failed_deps=True, is_final=True),
    ]
