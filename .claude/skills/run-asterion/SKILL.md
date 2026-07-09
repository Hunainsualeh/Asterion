---
name: run-asterion
description: Start, restart, or smoke-test the Asterion stack (FastAPI backend + Next.js frontend + Redis) and confirm a change actually works in the running app. Use when asked to run the app, reproduce a bug, or verify behaviour end-to-end rather than by reading code.
---

# Running Asterion

## Start it

Two terminals. Both commands are directory-sensitive.

```bash
# Terminal 1 — backend on http://127.0.0.1:8000
cd backend
.venv/Scripts/python.exe dev.py

# Terminal 2 — frontend on http://localhost:3000
cd frontend
npm run dev
```

Optionally start native Redis first: `D:\redis\start-redis.bat`.

## Three ways this goes wrong

**Using `uvicorn app.main:app --reload`.** Don't. It watches all of `backend/`,
including `workspace/` — where the agents write code. Every file an agent
creates trips the reloader mid-run: the server restarts, the running DAG task
dies (agents look like they "stopped spawning"), and in-process fakeredis state
is wiped, so the project 404s afterwards. `dev.py` watches only `app/`.

**Using system Python instead of the venv.** `.venv/Scripts/python.exe` has the
dependency set; `python` on PATH does not. A confusing `ModuleNotFoundError` for
`groq` or `langgraph` means this.

**A stale server.** Editing a file the reloader doesn't watch (or one it watched
while crashed) leaves an old process serving old code, and you debug a ghost.
If behaviour contradicts the source, confirm what's actually listening:

```bash
netstat -ano | grep :8000        # find the PID
curl -s -m 5 http://localhost:8000/health
```

Then kill it and restart. Verifying against a stale server has burned this
project before — check before you theorise.

## No Redis? That's fine, but know what you lost

Without native Redis the backend logs a loud warning and falls back to
in-process **fakeredis**. Runs work. But state is not durable and not shared
across processes, so: a backend restart loses every project, and anything that
reads Redis from a second process sees an empty store. If a project vanishes
after a restart, this is why — not a bug.

## Health checks

```bash
curl -s -m 5 http://localhost:8000/health          # {"status":"ok","redis":"native"|"fakeredis"}
curl -s -m 5 http://localhost:8000/api/projects
curl -s -m 15 http://localhost:8000/api/models     # catalog + which model is selected
curl -s -m 30 http://localhost:8000/api/models/health   # round-trips a real completion per provider
```

`/api/models/health` spends a few tokens per provider. It's the only check that
distinguishes a valid API key from a valid-but-unfunded one (DeepSeek is prepaid
and returns HTTP 402 on every completion at zero balance, while still
authenticating and listing its models).

## Verify a change without the UI

Import the module and drive it. The app is plain async Python:

```bash
cd backend
.venv/Scripts/python.exe -c "
import asyncio
from app.llm.client import chat_completion
async def go():
    r = await chat_completion([{'role':'user','content':'Reply with: OK'}], max_tokens=5)
    print(r.model, '->', r.choices[0].message.content)
asyncio.run(go())"
```

Route handlers are coroutines and can be called directly — no server, no
TestClient — which is the fastest way to check an endpoint's shape:

```bash
.venv/Scripts/python.exe -c "
import asyncio
from app.api.routes import models
print(asyncio.run(models.list_models()))"
```

## Tests

```bash
cd backend  && .venv/Scripts/python.exe -m pytest -q
cd frontend && npx tsc --noEmit
```

**Two tests fail on a clean tree.** They are not yours:
`tests/test_intent.py::test_build_request_routes_to_project_lane` and
`tests/test_dag_engine.py::test_cancellation_stops_pending_and_running`.
Expect `2 failed, N passed`. If a *third* fails, that one is yours.

Console output mangles em-dashes into `?` on this Windows shell. That is the
terminal encoding, not corrupted data — don't chase it.
