"""Async Redis client with an in-process fakeredis fallback.

The stack calls for Redis as the task-queue / memory / pub-sub backbone. Native
Redis (portable Windows build, no installer/Docker) runs from D:\\redis via
D:\\redis\\start-redis.bat. If it isn't up when the backend starts, we
transparently fall back to `fakeredis` (in-process) with a loud warning so the
pipeline still runs, but without durable or cross-process state.
"""
from __future__ import annotations

import logging

from app.config import get_settings

log = logging.getLogger("asterion.redis")

_client = None            # cached singleton
_backend = "uninitialized"  # one of: "native", "fakeredis"


async def get_redis():
    """Return a shared async Redis client (native if available, else fakeredis)."""
    global _client, _backend
    if _client is not None:
        return _client

    settings = get_settings()
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.redis_url,
            decode_responses=False,        # store raw bytes (vectors are binary)
            socket_connect_timeout=2,
            # Must exceed the longest BLOCK we issue (events.py's SSE tail blocks
            # up to 15s) - otherwise the client times out an intentional, healthy
            # block and kills the SSE stream (surfaced to the browser as
            # ERR_INCOMPLETE_CHUNKED_ENCODING).
            socket_timeout=20,
            protocol=2,                    # this Windows Redis build (5.0.14) predates RESP3/HELLO
        )
        await client.ping()
        _client = client
        _backend = "native"
        log.info("Connected to native Redis at %s", settings.redis_url)
    except Exception as exc:  # noqa: BLE001 - any connection failure triggers fallback
        if not settings.allow_fakeredis:
            raise
        import fakeredis.aioredis as fake

        _client = fake.FakeRedis(decode_responses=False)
        _backend = "fakeredis"
        log.warning(
            "Native Redis unreachable (%s). Falling back to in-process fakeredis. "
            "State is NOT durable or shared across processes. "
            "Start it with D:\\redis\\start-redis.bat, then retry.",
            exc,
        )
    return _client


def backend_kind() -> str:
    """Return which backend is in use: 'native', 'fakeredis', or 'uninitialized'."""
    return _backend


async def close_redis() -> None:
    global _client, _backend
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001
            pass
        _client = None
        _backend = "uninitialized"


def key(*parts: str) -> str:
    """Build a namespaced Redis key: asterion:part1:part2..."""
    ns = get_settings().redis_namespace
    return ":".join([ns, *parts])
