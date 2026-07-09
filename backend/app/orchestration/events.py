"""Pipeline event stream.

Agents publish progress events to a per-project Redis Stream. The SSE endpoint
tails this stream to drive the live UI. Using a Stream (not bare pub/sub) means
a client that connects late can replay history.

Every event carries two parallel readings of the same fact: `message` (plus
`kind`/`agent`/`data`) is the technical log line, and `friendly` is the
conversational translation of it — see `app.orchestration.stages`. The chat UI
renders `friendly`; the optional "Activity" drawer renders the raw fields for
anyone who wants the technical view.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any

from app.orchestration.stages import describe_event
from app.redis.client import get_redis, key
from app.services import project_store as store

MAXLEN = 2000  # cap stream length per project


def stream_key(project_id: str) -> str:
    return key("events", project_id)


async def publish_event(
    project_id: str,
    kind: str,
    agent: str = "",
    message: str = "",
    data: dict[str, Any] | None = None,
) -> None:
    """Append an event to the project's stream and update its live stage."""
    r = await get_redis()
    friendly = describe_event(kind, agent, message, data)
    event = {
        "kind": kind,          # e.g. agent_started, agent_message, tool_call, gate, done
        "agent": agent,
        "message": message,
        "data": data or {},
        "friendly": asdict(friendly),
        "ts": time.time(),
    }
    await r.xadd(stream_key(project_id), {"e": json.dumps(event)}, maxlen=MAXLEN, approximate=True)
    # Persisted separately (not just replayed from the stream) so a plain REST
    # poll of /projects/{id} always reflects exactly what's happening right
    # now, even for a client that never opens the SSE connection.
    await store.set_stage(project_id, {"kind": kind, "agent": agent, "friendly": asdict(friendly)})


async def read_events(project_id: str, last_id: str = "0-0", block_ms: int = 15000, count: int = 50):
    """Read events after `last_id`. Blocks up to block_ms for new ones."""
    r = await get_redis()
    result = await r.xread({stream_key(project_id): last_id}, count=count, block=block_ms)
    out: list[tuple[str, dict[str, Any]]] = []
    if result:
        for _stream, entries in result:
            for entry_id, fields in entries:
                raw = fields.get(b"e") or fields.get("e")
                if isinstance(raw, bytes):
                    raw = raw.decode()
                eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                out.append((eid, json.loads(raw)))
    return out
