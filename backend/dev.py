"""Dev launcher — reload ONLY on source edits, never on the agents' own output.

Running `uvicorn app.main:app --reload` watches the whole backend directory,
which includes `workspace/`. Every file an agent writes there (e.g.
`repo/calculator.py`) then trips the reloader mid-run: the server restarts, the
running DAG task is killed (agents appear to "stop spawning"), and the
in-process fakeredis state is wiped (the project 404s afterward). Watching only
`app/` fixes that while keeping hot-reload for real source changes.

Usage:  python dev.py   (from the backend/ directory)

For fully durable, restart-proof runs, also start native Redis
(D:\\redis\\start-redis.bat) so state survives even a deliberate source reload.
"""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        # Only watch source. NOT workspace/, tasks.db, knowledge.db, etc.
        reload_dirs=[os.path.join(here, "app")],
    )
