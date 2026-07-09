"""Test plumbing: run `async def` tests on a fresh event loop.

Keeps the suite dependency-free (no pytest-asyncio); `@pytest.mark.asyncio`
markers are tolerated but not required.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


@pytest.fixture(autouse=True)
def _reset_redis_client():
    """Each test runs on its own event loop (asyncio.run), but the redis
    module caches one client bound to whichever loop created it. Drop the
    cache between tests so every loop gets its own (fake)redis client."""
    yield
    from app.redis import client

    client._client = None
    client._backend = "uninitialized"


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: async test (run via asyncio.run)")


def pytest_pyfunc_call(pyfuncitem):
    fn = pyfuncitem.function
    if inspect.iscoroutinefunction(fn):
        kwargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(fn(**kwargs))
        return True
    return None
