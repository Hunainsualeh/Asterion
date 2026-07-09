"""Task understanding & routing.

Every incoming request is classified BEFORE any orchestration starts, so a
simple question ("what's the weather in Lahore?") gets a one-node fast path
instead of waking the full SDLC pipeline, and a real build request still gets
the multi-agent treatment.

Two layers, cheapest first:
  1. Deterministic heuristics — regexes for unambiguous shapes (weather,
     greetings, short factual questions). Zero tokens, zero latency.
  2. LLM classification on the fast model with a JSON response format,
     few-shot prompted. Any failure falls back to a conservative heuristic
     guess rather than blocking the request.

The result is an `Intent` with a kind, a complexity score, and extracted
slots (e.g. the weather location) that the DAG builder uses to shape the run.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("asterion.intent")

# What the system can route to.
INTENT_KINDS = (
    "chat",              # greetings/thanks/small talk — answer directly
    "weather",           # weather/forecast questions — weather tool fast path
    "search",            # "look up X" / current-events questions — web search
    "qa",                # general factual/how-to question — direct answer (+search if needed)
    "coding",            # write/fix a script or snippet — code task, no SDLC ceremony
    "research",          # multi-source investigation → structured findings
    "analysis",          # analyze/compare/evaluate something
    "writing",           # draft/edit prose (emails, posts, docs)
    "data",              # data processing/transformation questions
    "software_project",  # build an actual application/service — full SDLC pipeline
    "task_command",      # manage the user's OWN tasks/reminders — Task Agent
    "system_control",    # drive the app itself (open settings, new chat, theme) — action layer
)

COMPLEXITY_LEVELS = ("trivial", "simple", "moderate", "complex")


@dataclass
class Intent:
    kind: str
    complexity: str = "simple"
    confidence: float = 0.5
    reason: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    source: str = "heuristic"  # heuristic | llm | fallback
    # Clarifying questions worth asking BEFORE building (only for dev tasks
    # where a missing decision materially changes the implementation).
    questions: list[str] = field(default_factory=list)

    @property
    def lane(self) -> str:
        """Which execution lane handles this intent.

        Only a genuinely complex build gets the full SDLC pipeline (the
        7-agent team with approval gates). A moderate dev task — dashboard,
        CRUD app, small multi-file feature — runs in the task lane on the
        lighter plan → develop → review chain instead of waking everyone up.
        """
        return "project" if self.kind == "software_project" and self.complexity == "complex" else "task"

    def normalized(self) -> "Intent":
        """A software_project that isn't complex runs as a moderate coding
        task (plan → develop → review), not the full pipeline."""
        if self.kind == "software_project" and self.lane == "task":
            self.kind = "coding"
            if self.complexity in ("trivial", "simple"):
                self.complexity = "moderate"
        return self

    def as_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "complexity": self.complexity,
            "confidence": self.confidence,
            "reason": self.reason,
            "slots": self.slots,
            "source": self.source,
            "lane": self.lane,
            "questions": self.questions,
        }


# ---------------------------------------------------------------------------
# Layer 1 — deterministic heuristics
# ---------------------------------------------------------------------------
_WEATHER_RE = re.compile(
    r"\b(weather|forecast|temperature|how (hot|cold|warm)|rain(ing)?|snow(ing)?|humidity|climate today)\b",
    re.IGNORECASE,
)
_WEATHER_LOC_RE = re.compile(r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z .'-]{1,40}?)(?:[?.!,]|$)", re.IGNORECASE)
_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|yo|salam|assalam[ou] ?alaikum|good (morning|afternoon|evening)|thanks?|thank you|ok|okay)[\s!.?]*$",
    re.IGNORECASE,
)
# Verbs that signal an actual build request (needed beyond a question).
_BUILD_RE = re.compile(
    r"\b(build|create|develop|implement|make)\b.{0,60}\b(app|application|website|web ?app|site|api|service|platform|dashboard|tool|system|bot|game|extension|saas|mvp|backend|frontend)\b",
    re.IGNORECASE | re.DOTALL,
)
_CODING_RE = re.compile(
    r"\b(write|fix|debug|refactor|convert|optimi[sz]e)\b.{0,50}\b(script|function|snippet|regex|query|code|program|class|method)\b",
    re.IGNORECASE | re.DOTALL,
)
# System-control: imperative + a known in-app target. High precision on purpose
# — these must never trip on "build a settings page" (that's a dev task).
_CONTROL_RE = re.compile(
    r"^\s*(?:please\s+)?(open|show|go to|take me to|navigate to|create|start|make|new|delete|remove|"
    r"switch|toggle|change|close|hide)\b[^.?!]{0,30}?\b(settings?|preferences|new chat|"
    r"another chat|this (?:chat|conversation)|the chat|tasks?|reminders?|notifications?|"
    r"notification (?:center|centre)|profile|account|theme|dark mode|light mode|sidebar)\b",
    re.IGNORECASE,
)
# Task-command: reminder-shaped requests for the USER's own to-dos. Kept tight
# ("remind me…", "set a reminder…") so "create a task manager app" stays a build.
# Lifecycle verbs only count when they target a reminder/task/to-do — that's
# what separates "delete my gym reminder" (task) from "delete this chat" (nav).
_TASK_CMD_RE = re.compile(
    r"^\s*(?:please\s+)?("
    r"remind me\b|set (?:a|an|up a)? ?reminder\b|add (?:a )?(?:reminder|task)\b|snooze\b|"
    r"(?:reschedule|move|delete|remove|cancel|complete|finish|mark|update|rename|change)\b"
    r"[^.?!]{0,40}\b(?:reminder|task|to-?do)\b)",
    re.IGNORECASE,
)
# "show/list my tasks/reminders" is a task query, not navigation.
_TASK_QUERY_RE = re.compile(
    r"^\s*(?:show|list|what are|whats|what's|do i have)\b[^.?!]{0,30}\b(tasks?|reminders?|to-?dos?|due)\b",
    re.IGNORECASE,
)
# Bulk chat/project deletion ("delete all my chats", "clear all projects").
_CONTROL_BULK_RE = re.compile(
    r"\b(delete|remove|clear|wipe|discard|trash)\b[^.?!]{0,20}\b(all|every|my)\b"
    r"[^.?!]{0,16}\b(chats?|conversations?|projects?)\b",
    re.IGNORECASE,
)


def classify_heuristic(text: str) -> Intent | None:
    """Fast, deterministic classification for unambiguous inputs. Returns
    None when the shape is ambiguous and the LLM should decide."""
    stripped = text.strip()
    if _GREETING_RE.match(stripped):
        return Intent(kind="chat", complexity="trivial", confidence=0.98, reason="greeting/acknowledgement", source="heuristic")

    # Task/reminder management comes before generic build/coding detection so a
    # "remind me to finish the app" isn't misread as a software request.
    if _TASK_CMD_RE.search(stripped) or _TASK_QUERY_RE.search(stripped):
        return Intent(kind="task_command", complexity="simple", confidence=0.9,
                      reason="task/reminder management", source="heuristic")

    # Bulk "delete/clear all chats/projects" — precise so it never lands in the
    # chat lane (where the model would only *talk* about deleting). Checked with
    # no length guard because it's already an unambiguous shape.
    if _CONTROL_BULK_RE.search(stripped):
        return Intent(kind="system_control", complexity="trivial", confidence=0.92,
                      reason="bulk delete chats/projects", source="heuristic")

    # In-app navigation / control.
    if _CONTROL_RE.search(stripped) and len(stripped.split()) <= 8:
        return Intent(kind="system_control", complexity="trivial", confidence=0.9,
                      reason="app navigation/control", source="heuristic")

    if _WEATHER_RE.search(stripped) and len(stripped) < 140:
        loc = None
        m = _WEATHER_LOC_RE.search(stripped)
        if m:
            loc = m.group(1).strip()
        return Intent(
            kind="weather",
            complexity="trivial",
            confidence=0.95,
            reason="weather question",
            slots={"location": loc} if loc else {},
            source="heuristic",
        )

    if _BUILD_RE.search(stripped) and len(stripped.split()) >= 4:
        # An explicit build request is a dev task for sure — but how MUCH of
        # one (simple snippet vs. planner+developer vs. the full team) depends
        # on scope words and length; let the LLM tier it.
        return None

    if _CODING_RE.search(stripped):
        return Intent(kind="coding", complexity="simple", confidence=0.8, reason="single coding task", source="heuristic")

    # Short question with a question mark and no build language → simple QA.
    if stripped.endswith("?") and len(stripped.split()) <= 12:
        return Intent(kind="qa", complexity="simple", confidence=0.7, reason="short direct question", source="heuristic")

    return None


# ---------------------------------------------------------------------------
# Layer 2 — LLM classification (fast model, JSON mode)
# ---------------------------------------------------------------------------
_CLASSIFY_PROMPT = """You are the routing brain of an engineering assistant. Classify the user's request so the right amount of machinery runs — casual chat must NOT spawn a project, and a real project must NOT get a one-liner.

Return ONLY a JSON object:
{"kind": "<one of: chat, weather, search, qa, coding, research, analysis, writing, data, software_project, task_command, system_control>",
 "complexity": "<one of: trivial, simple, moderate, complex>",
 "reason": "<one short sentence>",
 "slots": {"location": "<city if weather>", "topic": "<main subject>"},
 "questions": ["<clarifying question>", ...]}

The four tiers (pick the LOWEST tier that fully covers the request):
1. Conversation — greetings, small talk, opinions, explanations, definitions, factual questions.
   -> kind chat/qa/search/weather, complexity trivial or simple. NEVER a dev tier.
2. Simple dev task — one script, one function, one snippet, one small HTML page, a quick bug fix.
   -> kind "coding", complexity "simple" (or "trivial").
3. Moderate dev task — multi-page app, dashboard, CRUD system, API integration, auth flow, a medium feature spanning several files.
   -> kind "coding", complexity "moderate". Also use this when someone says "build an app" but the app is small (calculator, todo list, landing page, simple game).
4. Complex software project — SaaS platform, full-stack product, multi-service system, production-grade platform with several major components.
   -> kind "software_project", complexity "complex". Reserve this for requests that genuinely need requirements, architecture, and phased delivery.

Other kinds: "research" = multi-angle investigation (parallel lookups); "analysis" = compare/evaluate; "writing" = prose drafting; "data" = data processing.

TWO special kinds that are NOT dev work and NOT conversation:
- "task_command": the user manages their OWN reminders/to-dos — "remind me tomorrow at 9am to submit my visa documents", "add a task to pay tuition next week", "reschedule my gym reminder to Friday", "show my upcoming tasks", "delete my blocked-account reminder". These are personal reminders, NOT software. CRITICAL: "build/make a task manager app", "create a to-do website" are software (kind coding/software_project), NOT task_command — the giveaway is whether they want *software built* vs. *a reminder set for themselves*.
- "system_control": the user tells the APP to do something to itself — "open settings", "create a new chat", "delete this chat", "show notifications", "open my profile", "switch the theme", "collapse the sidebar". Navigation and app controls, never a question about the world.
Both are complexity "trivial" and never get questions.

MODIFICATIONS: a request to change/fix/tweak/restyle code that was already built earlier in the conversation ("make the button blue", "add a pause key", "fix the score bug") is ALWAYS {"kind":"coding","complexity":"simple"} with no questions — it's a targeted edit, never a re-plan of the whole app.

"questions": ONLY for tier 2-4 requests, and ONLY when you literally cannot start building without the answer (platform web/CLI/mobile unknown, language ambiguous AND consequential, data source completely unspecified for a data app). Preferences that have an obvious default — page size, styling, which stats to show, optional features — are NEVER questions: pick the default and build. If the user already gave any concrete spec, return []. Max 2 questions. Conversation never gets questions.

Examples:
"Hi, how are you?" -> {"kind":"chat","complexity":"trivial","reason":"greeting","slots":{},"questions":[]}
"What is React?" -> {"kind":"qa","complexity":"trivial","reason":"definition question","slots":{"topic":"React"},"questions":[]}
"Write a python function to sort an array" -> {"kind":"coding","complexity":"simple","reason":"single function","slots":{"topic":"sort function"},"questions":[]}
"Make me a calculator" -> {"kind":"coding","complexity":"simple","reason":"small app, needs platform choice","slots":{"topic":"calculator"},"questions":["Web page, desktop, or command-line?","Basic arithmetic or scientific functions?"]}
"Create a CRUD dashboard for my inventory with charts" -> {"kind":"coding","complexity":"moderate","reason":"multi-page app with data layer","slots":{"topic":"inventory dashboard"},"questions":["Where does the inventory data live (database, API, file)?"]}
"Build a SaaS platform for team invoicing with billing, auth and an admin panel" -> {"kind":"software_project","complexity":"complex","reason":"multi-component product","slots":{"topic":"invoicing SaaS"},"questions":[]}
"Compare Postgres and MongoDB for analytics workloads" -> {"kind":"analysis","complexity":"moderate","reason":"comparison across criteria","slots":{"topic":"Postgres vs MongoDB"},"questions":[]}
"Remind me tomorrow at 9am to submit my visa documents" -> {"kind":"task_command","complexity":"trivial","reason":"personal reminder","slots":{"topic":"submit visa documents"},"questions":[]}
"Build me a to-do list app with reminders" -> {"kind":"coding","complexity":"moderate","reason":"software with a data layer","slots":{"topic":"to-do app"},"questions":[]}
"Open settings and switch to dark mode" -> {"kind":"system_control","complexity":"trivial","reason":"app navigation/control","slots":{},"questions":[]}
"""


async def classify_llm(text: str) -> Intent:
    from app.config import get_settings
    from app.llm.client import chat_completion

    response = await chat_completion(
        messages=[
            {"role": "system", "content": _CLASSIFY_PROMPT},
            {"role": "user", "content": text[:2000]},
        ],
        model=get_settings().groq_fast_model,
        temperature=0.0,
        max_tokens=400,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or "{}"
    data = json.loads(raw)
    kind = data.get("kind") if data.get("kind") in INTENT_KINDS else "qa"
    complexity = data.get("complexity") if data.get("complexity") in COMPLEXITY_LEVELS else "simple"
    slots = data.get("slots") if isinstance(data.get("slots"), dict) else {}
    questions = data.get("questions") if isinstance(data.get("questions"), list) else []
    if kind in ("chat", "qa", "search", "weather", "writing"):
        questions = []  # conversation never gets an interrogation
    return Intent(
        kind=kind,
        complexity=complexity,
        confidence=0.75,
        reason=str(data.get("reason", ""))[:200],
        slots={k: v for k, v in slots.items() if v},
        source="llm",
        questions=[str(q).strip() for q in questions[:3] if str(q).strip()],
    )


async def classify(text: str) -> Intent:
    """Full routing decision: heuristics first, LLM for the ambiguous rest,
    safe fallback if the LLM is unavailable."""
    heuristic = classify_heuristic(text)
    if heuristic is not None:
        return heuristic
    try:
        intent = await classify_llm(text)
        # Backstop against over-asking: a request detailed enough to fill a
        # sentence or two deserves sensible defaults, not a questionnaire.
        if len(text.split()) > 25:
            intent.questions = []
        return intent
    except Exception as exc:  # noqa: BLE001 — routing must never block the request
        log.warning("LLM intent classification failed (%s); using fallback", exc)
        # Conservative fallback: long, imperative texts look like project
        # briefs; everything else is treated as a general task.
        words = len(text.split())
        if words > 60:
            return Intent(kind="software_project", complexity="complex", confidence=0.4,
                          reason="long brief (fallback)", source="fallback")
        return Intent(kind="qa", complexity="simple", confidence=0.4, reason="fallback", source="fallback")
