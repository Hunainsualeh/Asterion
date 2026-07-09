# Asterion

A multi-agent software-development pipeline. A user describes an idea in chat; a
chain of specialised LLM agents runs Scope → Architecture → Tickets → Build →
Security → Review → Test → Debug → Docs, pausing at human approval gates, and
writes real code into a sandboxed workspace.

**Stack:** FastAPI + LangGraph + Redis (backend, Python 3.14) · Next.js 16 +
React 19 + Tailwind v4 (frontend) · Groq and DeepSeek (LLM providers).

## Hard project constraints

These come from the project owner and override any default instinct:

- **No Docker.** Do not add, suggest, or reference it. Redis runs as a portable
  Windows build from `D:\redis\start-redis.bat`.
- **No GitHub integration.** `app/tools/git_tools.py` drives a *local* git repo
  inside the agent workspace. Nothing pushes anywhere.
- **Never commit `.env`.** It holds live API keys.

## Running it

Two terminals. The backend must be started **from `backend/` using its own
venv** — a system-Python `uvicorn` will import a different dependency set.

```bash
# Terminal 1 — backend on :8000
cd backend
.venv/Scripts/python.exe dev.py

# Terminal 2 — frontend on :3000
cd frontend
npm run dev
```

Use `dev.py`, **not** `uvicorn app.main:app --reload`. Plain `--reload` watches
the whole `backend/` tree, which contains `workspace/` — so every file an agent
writes trips the reloader mid-run, killing the running DAG task and wiping
in-process fakeredis state (the project then 404s). `dev.py` watches only
`app/`.

Optional: start native Redis (`D:\redis\start-redis.bat`) before the backend.
Without it the app falls back to in-process **fakeredis** with a loud warning —
runs still work, but state is not durable and not shared across processes.

```bash
cd backend && .venv/Scripts/python.exe -m pytest -q     # tests
cd frontend && npx tsc --noEmit                         # typecheck
```

**Two tests fail on `main` and are unrelated to any current work** — don't
"fix" them as collateral, and don't treat them as a regression you caused:
`tests/test_intent.py::test_build_request_routes_to_project_lane` (the
`classify_heuristic` regex misses that phrasing) and
`tests/test_dag_engine.py::test_cancellation_stops_pending_and_running`.

## Where things live

```
backend/app/
  llm/            provider clients, model catalog, routing, selection   ← read below
  agents/         the six pipeline agents; base.py holds the tool-calling loop
  orchestration/  LangGraph pipeline: graph, stages, gates, intent, events
  dag/            the *other* lane: chat/task DAG (engine, workflows, task_runner)
  tools/          registry + every tool an agent may call (fs, shell, git, research…)
  tasks/          reminders/scheduling assistant platform (SQLite: tasks.db)
  knowledge/      embedding store + retrieval (SQLite: knowledge.db)
  api/routes/     FastAPI endpoints
  workspace/      ← agent-generated code. NEVER hand-edit; never watch with --reload.
frontend/
  app/components/ UI; hooks/ shared state; lib/ api client + voice model
```

### Two execution lanes

`api/routes/chat.py` classifies every message (`orchestration/intent.py`) and
sends it down one of two paths. Know which one you're touching:

- **Project lane** (`orchestration/`) — a real build request. LangGraph state
  machine with human gates (`APPROVE_SCOPE`, `APPROVE_ARCHITECTURE`,
  `APPROVE_TICKETS`, `MANUAL_TEST`). Agents run through
  `agents/base.py::run_tool_loop`.
- **Task lane** (`dag/`) — everything else: questions, analysis, research,
  one-off code. A dynamically expanded DAG of `exec_*` executors in
  `dag/workflows.py`. Also where reminders (`tasks/`) and app control
  (`control/`) get dispatched.

Both stream to the UI as SSE events via `orchestration/events.py`.

## The LLM layer — read this before touching any model call

Asterion talks to two providers. The rules that keep that manageable:

1. **Always call `app.llm.client.chat_completion`.** It resolves the model id
   to a provider and dispatches. Never import `groq` or `httpx` above
   `app/llm/` — and never import `groq_client` / `deepseek_client` directly
   except for provider-specific work (health checks, model discovery).

2. **Catch `app.llm.errors`, never SDK exceptions.** Both clients translate
   their SDK's errors at the boundary into one taxonomy, because each kind
   implies a different recovery:

   | Error | Meaning | Recovery |
   |---|---|---|
   | `MalformedToolCall` | model emitted an unparseable `<function=…>` blob | resample the same model |
   | `OverCapacity` | provider 5xx | back off, retry same model |
   | `RateLimited` / `KeyPoolExhausted` | quota gone right now | escalate to next model |
   | `ProviderUnavailable` | dead key, unpaid balance | escalate; only the user can fix it |
   | `RequestTooLarge` | transcript over this model's ceiling | escalate to a bigger context |
   | `LLMBadRequest` | we built a bad request | raise; retrying won't help |

   `agents/base.py::_complete_with_retry` is the one place that ladder is
   implemented. Don't duplicate it.

3. **Model routing has two layers.** `litellm_config.yaml` assigns each *agent*
   a model plus a fallback chain (Architect gets deep reasoning; Developer gets
   the high-quota fast model). On top, the user's pick in **Settings › Models**
   is an *override* (`llm/selection.py`) that gets **prepended** to every chain
   — the configured models stay behind it as fallbacks. That is precisely why
   selecting DeepSeek is safe on an unpaid account: the 402 raises
   `ProviderUnavailable`, the chain escalates, and the run finishes on Groq.

   Calls that pass an explicit `model=` (intent classification, chat titles,
   image reading, `groq/compound` web research) bypass the override on purpose:
   they are cheap infrastructure, not the user's conversation.

4. **Provider resolution is not "split on the first slash."** Two real ids
   break that, and both exist in this codebase:

   - `groq/compound` — a **Groq** model whose id literally starts with `groq/`.
   - `deepseek-r1-distill-llama-70b` — DeepSeek *weights*, **Groq** host.

   So `catalog.resolve()` routes to DeepSeek only on an explicit `deepseek/`
   prefix or a known DeepSeek catalog id; everything else is Groq. DeepSeek ids
   are namespaced (`deepseek/deepseek-v4-pro`) whenever they cross a boundary.

5. **DeepSeek's live model ids are `deepseek-v4-flash` and `deepseek-v4-pro`.**
   Not `deepseek-chat` / `deepseek-reasoner` — those are what almost every doc
   (and every model's training data) still says. `deepseek_client.list_models()`
   fetches the truth from `GET /models`; trust it over any static list.

6. **DeepSeek is prepaid and fails open confusingly.** A zero-balance key
   authenticates, lists models, and then answers *every* completion with HTTP
   402. `deepseek_client.status_note()` asks `/user/balance` so Settings ›
   Models warns the user before they pick, instead of after a run mysteriously
   completes on Groq.

Adding a provider or model? See `.claude/skills/llm-provider/SKILL.md`.

## Voice is currently disabled

The whole voice stack ("Friday", wake words, TTS) is **off behind two build
flags** in `frontend/lib/voice.ts`. Nothing was deleted.

```ts
export const VOICE_ENABLED = false;      // mic button, overlay, recognition, TTS, Settings › Voice
export const WAKE_WORD_ENABLED = false;  // background "Hey Friday" always-on mic
```

`VoiceProvider` returns an inert context before any hook runs, so `getUserMedia`
is never called. `useVoiceConfig` additionally **clamps** `wakeWordEnabled` on
load and on every write — existing users have `true` in `localStorage`, and
without the clamp, disabling the feature in code would do nothing for exactly
the people who already had it. Flip a flag to restore; no other edit needed.

## Conventions

- **Comments explain *why*, never *what*.** The existing code is dense with
  hard-won reasons (Chrome's `speechSynthesis` drops events after ~15s; Groq's
  8K TPM cap made `gpt-oss-120b` 413 on every code step). Match that. Never
  write a comment that narrates the diff or addresses a reviewer.
- **Tool changes go through the registry.** `app/tools/registry.py` enforces a
  per-agent allowlist inside `dispatch()`, so a tool-calling loop can't reach
  outside its agent's declared capabilities. Registering is the security
  boundary — don't bypass it.
- **Errors reaching the user go through `orchestration/stages.py::describe_error`.**
  It maps an exception to a title/explanation/suggestion and a `retryable` flag.
  A `ProviderUnavailable` must not tell the user to "wait and retry."
- **The context budget is enforced in the loop.** `agents/base.py::_trim_messages`
  keeps transcripts under `CONTEXT_CHAR_BUDGET` because free-tier TPM caps run
  as low as 6–8K tokens/request. Long tool loops 413 without it.
- **Frontend:** `frontend/AGENTS.md` applies — this Next.js version has
  breaking changes vs. training data. Read `node_modules/next/dist/docs/`
  before writing App Router code.

## Environment

Keys live in the project-root `.env` (a `backend/.env` may override):

| Variable | Notes |
|---|---|
| `Asterion_Secret_key`, `…1`, `…2`, `…3` | Groq. 4 keys pooled & rotated per-model on rate limits. Required. |
| `Deepseek_Key` | DeepSeek. Optional — without it the picker greys DeepSeek out. |
| `REDIS_URL` | Falls back to fakeredis if unreachable. |

`backend/.env.example` documents the rest.
